// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import React, { useEffect, useMemo, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import Tiff from 'tiff.js';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import { apiService } from '../services/api';

pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.mjs`;

const IMAGE_EXTENSIONS = /^(jpe?g|png|gif|webp|bmp)$/i;

function resolveFileType(fileUrl: string, fileType?: string): string {
    if (fileType) {
        return fileType.toLowerCase();
    }
    const path = fileUrl.split('?')[0] ?? '';
    return path.split('.').pop()?.toLowerCase() || 'unknown';
}

type DocumentViewerProps = {
    fileUrl: string;
    fileType?: string;
    zoomLevel?: number;
    imagePosition?: { x: number; y: number };
    isDragging?: boolean;
};

type PdfFileSource = string | { url: string; httpHeaders: Record<string, string> };

export default function DocumentViewer({
    fileUrl,
    fileType,
    zoomLevel = 100,
    imagePosition = { x: 0, y: 0 },
    isDragging = false,
}: DocumentViewerProps) {
    const type = useMemo(() => resolveFileType(fileUrl, fileType), [fileUrl, fileType]);
    const [images, setImages] = useState<string[]>([]);
    const [imageObjectUrl, setImageObjectUrl] = useState<string | null>(null);
    const [pdfFile, setPdfFile] = useState<PdfFileSource | null>(null);
    const [numPages, setNumPages] = useState<number | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const pdfContainerRef = React.useRef<HTMLDivElement>(null);

    const isImage = IMAGE_EXTENSIONS.test(type);

    useEffect(() => {
        setLoadError(null);
        setImages([]);
        setImageObjectUrl(null);
        setPdfFile(null);
        setNumPages(null);
    }, [fileUrl, type]);

    useEffect(() => {
        if (!isImage) {
            return;
        }

        let objectUrl: string | null = null;
        let cancelled = false;

        (async () => {
            try {
                objectUrl = await apiService.fetchBlobObjectUrl(fileUrl);
                if (!cancelled) {
                    setImageObjectUrl(objectUrl);
                }
            } catch (error) {
                console.error('Error loading image:', error);
                if (!cancelled) {
                    setLoadError(
                        'Unable to load image. The file may be missing or the access link may have expired.',
                    );
                }
            }
        })();

        return () => {
            cancelled = true;
            if (objectUrl) {
                URL.revokeObjectURL(objectUrl);
            }
        };
    }, [fileUrl, isImage]);

    useEffect(() => {
        if (type !== 'pdf') {
            return;
        }

        let cancelled = false;

        (async () => {
            try {
                const source = await apiService.getBlobProxyFileOptions(fileUrl);
                if (!cancelled) {
                    setPdfFile(source);
                }
            } catch (error) {
                console.error('Error preparing PDF source:', error);
                if (!cancelled) {
                    setLoadError('Unable to load PDF.');
                }
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [fileUrl, type]);

    useEffect(() => {
        if (type !== 'tiff' && type !== 'tif') {
            return;
        }

        (async () => {
            try {
                const useProxy = apiService.isAzureBlobStorageUrl(fileUrl);
                const response = useProxy
                    ? await apiService.fetchBlobContent(fileUrl)
                    : await fetch(fileUrl);
                if (!response.ok) {
                    throw new Error(`Failed to fetch TIFF (${response.status})`);
                }
                const buffer = await response.arrayBuffer();
                const tiff = new Tiff({ buffer });
                const pageCount = tiff.countDirectory();
                const urls: string[] = [];

                for (let i = 0; i < pageCount; i++) {
                    tiff.setDirectory(i);
                    const canvas = tiff.toCanvas();
                    if (canvas) urls.push(canvas.toDataURL());
                }
                setImages(urls);
            } catch (error) {
                console.error('Error loading TIFF:', error);
                setLoadError('Unable to load TIFF image.');
            }
        })();
    }, [fileUrl, type]);

    const onDocumentLoadSuccess = ({ numPages: pages }: { numPages: number }) => {
        setNumPages(pages);
        if (pdfContainerRef.current) {
            pdfContainerRef.current.scrollTop = 0;
        }
    };

    useEffect(() => {
        if (pdfContainerRef.current) {
            pdfContainerRef.current.scrollTop = 0;
        }
    }, [fileUrl]);

    const transformStyle: React.CSSProperties = {
        transform: `scale(${zoomLevel / 100}) translate(${imagePosition.x / (zoomLevel / 100)}px, ${imagePosition.y / (zoomLevel / 100)}px)`,
        transition: isDragging ? 'none' : 'transform 0.2s ease-out',
    };

    const commonClass = 'max-w-full max-h-full rounded-lg shadow-lg object-contain select-none';

    return (
        <div className="bg-museum-50 shadow-sm h-full min-h-[240px] w-full flex items-center justify-center">
            {loadError && (
                <p className="text-gray-500 px-4 text-center">{loadError}</p>
            )}

            {!loadError && isImage && imageObjectUrl && (
                <img
                    src={imageObjectUrl}
                    alt="Image Document"
                    className={commonClass}
                    style={transformStyle}
                    draggable={false}
                    onError={() =>
                        setLoadError(
                            'Unable to load image. The file may be missing or the access link may have expired.',
                        )
                    }
                />
            )}

            {!loadError && type === 'pdf' && pdfFile && (
                <div ref={pdfContainerRef} className="w-full h-full overflow-y-auto">
                    <div
                        style={{
                            transform: `scale(${zoomLevel / 100})`,
                            transformOrigin: 'top center',
                            transition: isDragging ? 'none' : 'transform 0.2s ease-out',
                        }}
                    >
                        <Document
                            file={pdfFile}
                            onLoadSuccess={onDocumentLoadSuccess}
                            onLoadError={() => setLoadError('Unable to load PDF.')}
                            loading={<div className="text-gray-500 p-4">Loading PDF...</div>}
                        >
                            {numPages ? (
                                Array.from(new Array(numPages), (_el, index) => (
                                    <div
                                        key={`page_${index + 1}`}
                                        className="mb-4 bg-white mx-auto"
                                        style={{ width: 'fit-content' }}
                                    >
                                        <Page
                                            pageNumber={index + 1}
                                            width={800}
                                            renderTextLayer={true}
                                            renderAnnotationLayer={true}
                                        />
                                    </div>
                                ))
                            ) : (
                                <div className="text-gray-500 p-4">Loading pages...</div>
                            )}
                        </Document>
                    </div>
                </div>
            )}

            {!loadError && (type === 'tiff' || type === 'tif') && images.length > 0 && (
                <div className={commonClass} style={transformStyle}>
                    {images.map((src, i) => (
                        <img
                            key={i}
                            src={src}
                            alt={`Page ${i + 1}`}
                            style={{
                                marginBottom: '5px',
                                background: 'white',
                                border: '1px solid #444',
                                maxWidth: '90%',
                                boxShadow: '0 0 6px rgba(0,0,0,0.5)',
                            }}
                        />
                    ))}
                </div>
            )}

            {!loadError && type === 'unknown' && (
                <p className="text-gray-500 px-4 text-center">
                    Unsupported file type for preview.
                </p>
            )}
        </div>
    );
}
