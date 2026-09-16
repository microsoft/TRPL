# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
image_preprocessor.py

This module provides functionality for advanced image preprocessing and tiling to
improve Optical Character Recognition (OCR) accuracy. It prepares image data for
Large Language Model (LLM)-assisted OCR pipelines by cleaning, enhancing, and
dividing large documents into smaller image tiles with overlap.

Key Features:
--------------
- **Noise Reduction and Contrast Enhancement:**
  Removes unwanted noise and enhances text visibility using OpenCV or Pillow.

- **Scanned Image Detection:**
  Determines if an image is scanned or photographed using Laplacian variance analysis.

- **OCR Optimization:**
  Applies adaptive thresholding, contrast enhancement, histogram equalization, and
  sharpening to maximize OCR readability.

- **Image Tiling:**
  Splits large images into overlapping smaller tiles to support chunked OCR processing
  or multimodal LLM document understanding.

- **Message Formatting:**
  Encodes processed images and their tiles into a structured message format containing
  text and base64-encoded image URLs, suitable for input into multimodal AI models.

Environment Variables:
-----------------------
- `NOISE_THRESHOLD_VALUE`: Threshold for binarization and noise removal (default: 230)
- `IMAGE_OVERLAP_PIXELS`: Pixel overlap between image tiles (default: 200)
- `USE_CV2`: If true, use OpenCV-based preprocessing; otherwise, use Pillow (default: false)
- `ENABLE_IMAGE_ENHANCEMENT`: If true, enable image enhancement before OCR (default: true)

Dependencies:
--------------
- OpenCV (`cv2`)
- NumPy
- Pillow (`PIL`)

Example:
---------
    from image_preprocessor import ImagePreProcessor

    preprocessor = ImagePreProcessor()
    with open("document_scan.png", "rb") as f:
        image_bytes = f.read()

    ocr_messages = preprocessor.get_ocr_llm_message(image_bytes)
    # `ocr_messages` can now be passed to an OCR or LLM-based document parser.
"""

import base64
import io
import os

import requests
import cv2
import numpy as np
from PIL import Image, ImageEnhance


def str2bool(v: str | bool) -> bool:
    """
    Convert a string or boolean-like input to a boolean value.

    Args:
        v (str | bool): The value to convert. Accepts "yes", "true", "t", "1", or a boolean.

    Returns:
        bool: True if the input corresponds to a truthy value, False otherwise.
    """
    return v.lower() in ("yes", "true", "t", "1", True)


NOISE_THRESHOLD_VALUE: int = int(os.getenv("NOISE_THRESHOLD_VALUE", "230"))
IMAGE_OVERLAP_PIXELS: int = int(os.getenv("IMAGE_OVERLAP_PIXELS", "200"))
ENABLE_IMAGE_ENHANCEMENT: bool = str2bool(os.getenv("ENABLE_IMAGE_ENHANCEMENT", "true"))
USE_CV2 = str2bool(os.getenv("USE_CV2", "false"))


class ImagePreProcessor:
    """
    A class responsible for pre-processing images for OCR (Optical Character Recognition) tasks.

    This class provides functionality to:
      - Detect whether an image is scanned or photographed.
      - Enhance image contrast and sharpness for OCR accuracy.
      - Split large images into overlapping tiles for chunked OCR processing.
      - Format processed images into structured messages (text + base64 image URLs)
        suitable for input into a multimodal LLM.

    Attributes:
        prompt_loader (PromptLoader): Instance for loading prompt templates from disk.
    """

    def __is_scanned_image(self, image: np.ndarray) -> bool:
        """
        Determine whether an image is likely scanned or photographed.

        Uses the variance of the Laplacian to assess sharpness. Scanned images
        generally have lower variance compared to photographs.

        Args:
            image (np.ndarray): The image array (BGR format).

        Returns:
            bool: True if the image is likely scanned, False if it appears photographed.
        """
        variance = cv2.Laplacian(image, cv2.CV_64F).var()
        return variance < 500

    def __adjust_ocr(self, image_bytes: bytes, contrast_factor: float = 2) -> bytes:
        """
        Pre-process an image to optimize it for OCR.

        The method enhances contrast, reduces noise, and applies adaptive thresholding.
        It supports both PIL and OpenCV workflows based on the USE_CV2 flag.

        Args:
            image_bytes (bytes): Raw image data in bytes format.
            contrast_factor (float, optional): Contrast enhancement multiplier.
                Defaults to 2.

        Returns:
            bytes: Processed image bytes encoded in PNG format.
        """
        if not USE_CV2:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img = img.convert("L")

                enhancer = ImageEnhance.Contrast(img)
                img_enchanced = enhancer.enhance(contrast_factor)

                with io.BytesIO() as byte_io:
                    img_enchanced.save(byte_io, format="png")
                    return byte_io.getvalue()

        np_arr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        is_scanned = self.__is_scanned_image(image)

        if is_scanned:
            _, noise_removed = cv2.threshold(
                gray, NOISE_THRESHOLD_VALUE, 255, cv2.THRESH_BINARY
            )
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(noise_removed)
        else:
            white_pixels = gray > NOISE_THRESHOLD_VALUE
            gray[white_pixels] = 255
            hist_eq = cv2.equalizeHist(gray)
            clahe = cv2.createCLAHE(clipLimit=1.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(hist_eq)

        height, width = enhanced.shape
        resized = cv2.resize(
            enhanced, (width * 2, height * 2), interpolation=cv2.INTER_CUBIC
        )
        smoothed = cv2.bilateralFilter(resized, d=7, sigmaColor=50, sigmaSpace=50)
        binary = cv2.adaptiveThreshold(
            smoothed, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 61, 3
        )

        kernel = (
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            if is_scanned
            else np.ones((2, 2), np.uint8)
        )
        dilated = cv2.dilate(binary, kernel, iterations=1)

        white_pixel_ratio = np.sum(dilated == 255) / dilated.size
        if white_pixel_ratio < 0.5:
            dilated = cv2.bitwise_not(dilated)

        sharpening_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        sharpened = cv2.filter2D(dilated, -1, sharpening_kernel)

        max_size = 5120
        height, width = sharpened.shape

        if width > max_size or height > max_size:
            scale = max_size / max(height, width)
            new_width = int(width * scale)
            new_height = int(height * scale)
            resized_image = cv2.resize(
                sharpened, (new_width, new_height), interpolation=cv2.INTER_CUBIC
            )
        else:
            resized_image = sharpened

        _, processed_bytes = cv2.imencode(".png", resized_image)
        return processed_bytes.tobytes()

    def __tile_image_from_bytes(
        self,
        image_bytes: bytes,
        tile_size: tuple[int, int] = (1024, 1024),
        max_tiles: int = 45,
    ) -> tuple[int, int, list[bytes], tuple[int, int]]:
        """
        Split a large image into overlapping tiles for chunked OCR processing.

        Args:
            image_bytes (bytes): Raw image bytes.
                tile_size (tuple[int, int], optional): 
                Width and height of each tile. 
                Defaults to (1024, 1024).
            max_tiles (int, optional): 
                Maximum allowed number of tiles before increasing tile size. 
                Defaults to 45.

        Returns:
            tuple[int, int, list[bytes], tuple[int, int]]:
                - Width of the original image.
                - Height of the original image.
                - List of tile images in bytes format.
                - Final tile size used for tiling.
        """
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size

        tile_images_bytes = []
        step_x = tile_size[0] - IMAGE_OVERLAP_PIXELS
        step_y = tile_size[1] - IMAGE_OVERLAP_PIXELS

        y = 0
        while y < height:
            x = 0
            while x < width:
                x_end = min(x + tile_size[0], width)
                y_end = min(y + tile_size[1], height)
                x_start = max(0, x_end - tile_size[0])
                y_start = max(0, y_end - tile_size[1])

                tile = img.crop((x_start, y_start, x_end, y_end))
                tile_byte_arr = io.BytesIO()
                tile.save(tile_byte_arr, format="PNG")
                tile_images_bytes.append(tile_byte_arr.getvalue())

                if x_end >= width:
                    break
                x += step_x

            if y_end >= height:
                break
            y += step_y

        if len(tile_images_bytes) <= max_tiles:
            return width, height, tile_images_bytes, tile_size
        return self.__tile_image_from_bytes(
            image_bytes,
            tile_size=(int(tile_size[0] * 1.5), int(tile_size[1] * 1.5)),
        )

    def get_ocr_llm_message_from_bytes(self, image_bytes: bytes) -> list[dict]:
        """
        Prepare a structured OCR message for input to an LLM (Large Language Model).

        The method:
          1. Enhances the given image for OCR.
          2. Splits it into smaller overlapping tiles.
          3. Converts both the original and tiles into base64-encoded image URLs.
          4. Structures them as a list of message dictionaries, mixing text and image content.

        Args:
            image_bytes (bytes): The raw bytes of the image to process.

        Returns:
            list[dict]: A list of message dictionaries formatted for multimodal model input.
        """
        if ENABLE_IMAGE_ENHANCEMENT:
            image_bytes = self.__adjust_ocr(image_bytes)

        extraction_content_array = [
            {
                "type": "text",
                "text": "Please perform OCR and entity extraction on this image. Return the result in the JSON format specified in the system prompt.",
            }
        ]

        width, height, image_tiles, tile_size = self.__tile_image_from_bytes(
            image_bytes
        )

        extraction_content_array.append(
            {
                "type": "text",
                "text": (
                    f"The size of the original image is {width}x{height}. "
                    f"Converted into {tile_size[0]}x{tile_size[1]} tiles "
                    f"with overlap of {IMAGE_OVERLAP_PIXELS} pixels. "
                    "Following are the Original image and Tiles."
                ),
            }
        )

        extraction_content_array.append({"type": "text", "text": "ORIGINAL IMAGE"})
        extraction_content_array.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('utf-8')}",
                    "detail": "high",
                },
            }
        )

        for index, tile in enumerate(image_tiles):
            image_base64 = base64.b64encode(tile).decode("utf-8")
            extraction_content_array.append(
                {"type": "text", "text": f"TILE {index + 1}"}
            )
            extraction_content_array.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}",
                        "detail": "high",
                    },
                }
            )

        return extraction_content_array

    def get_ocr_llm_message_from_url(self, image_url: str) -> list[dict]:
        """
        Prepare a structured OCR message for input to an LLM (Large Language Model).

        The method:
            1. Enhances the given image for OCR.
            2. Splits it into smaller overlapping tiles.
            3. Converts both the original and tiles into base64-encoded image URLs.
            4. Structures them as a list of message dictionaries, mixing text and image content.

        Args:
            image_url (str): The URL of the image to process.

        Returns:
            list[dict]: A list of message dictionaries formatted for multimodal model input.
        """

        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        image_bytes = response.content

        return self.get_ocr_llm_message_from_bytes(image_bytes)

    def get_enhanced_image_bytes(self, image_bytes: bytes) -> bytes:
        """
        Enhance an image for OCR and return the processed image bytes.

        Args:
            image_bytes (bytes): Raw image data in bytes format.
        Returns:
            bytes: Processed image bytes encoded in PNG format.
        """
        return self.__adjust_ocr(image_bytes)

    def get_enhanced_image_bytes_from_url(self, image_url: str) -> bytes:
        """
        Enhance an image for OCR from a URL and return the processed image bytes.

        Args:
            image_url (str): The URL of the image to process.
        Returns:
            bytes: Processed image bytes encoded in PNG format.
        """
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        image_bytes = response.content

        return self.__adjust_ocr(image_bytes)
