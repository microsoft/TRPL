Extract metadata from OCR-transcribed letters written by or addressed to Theodore Roosevelt and output the information into the specified JSON schema. The metadata should describe the title, description, creator, recipient, resource type, production method, creation date, period, citation, rights, and language of the letter.

The output must be structured with `extracted_metadata` containing all the metadata fields, and `metadata_extraction_confidence` at the root level providing the overall confidence score.

**Important:** 
- **REQUIRED FIELDS:** "Title" and "Description" are MANDATORY and must NEVER be empty. You must always provide a value for these two fields, even if you need to infer or generate them from the letter content.
- For all other fields: If you are uncertain about any field, leave it as an empty string `""` rather than using placeholders. Only populate fields when you have reasonable confidence in the extracted value.

# Steps

1. **Title Extraction (REQUIRED):**

   - **This field is MANDATORY and must never be empty.**
   - First, try to identify the subject or title from the letter header or introductory line of the text.
   - If no explicit title is found in the text, you MUST generate a descriptive title based on the letter's content, subject matter, or main topic.
   - The title should be concise (typically 5-15 words) and capture the essence or primary subject of the letter.
   - Examples of generated titles when not explicit: "Letter regarding policy measures", "Correspondence about conservation efforts", "Letter from [Creator] to [Recipient] regarding [topic]".

2. **Description Extraction (REQUIRED):**

   - **This field is MANDATORY and must never be empty.**
   - Extract a brief summary or description of the letter's content, purpose, or main subject matter.
   - This should be a concise description (typically 1-3 sentences) that captures the essence of the letter.
   - You MUST always provide a description based on the letter's content. Even if the content is minimal or unclear, provide a description that summarizes what can be determined from the available text.
   - The description should include key topics, purpose, or main points discussed in the letter.

3. **Creation Date Extraction:**

   - Locate the date of the letter in the text; this is typically found in the header.
   - Use the ISO format `YYYY-MM-DD` for the date.
   - If no exact date is found, the date appears ambiguous, or you are uncertain, leave it as an empty string `""`.
   - For a date if it is partially mentioned (e.g., "January 3rd" without a year) and you are confident about the partial information, you may use `XXXX-01-03`, otherwise leave it empty.

4. **Creator Identification:**

   - Identify the creator (who authored the letter). This is often the signature line or explicit mention in the content.
   - If uncertain or unspecified, leave it as an empty string `""`.

5. **Recipient Identification:**

   - Identify the recipient (the addressee of the letter). Look for a salutation (e.g., "Dear Mr. Roosevelt") or explicit names in the body.
   - If uncertain or unspecified, leave it as an empty string `""`.

6. **Resource Type Extraction:**

   - Determine the type of resource/document. For letters, this is typically "Letter" or "Correspondence".
   - Other possible values might include "Manuscript", "Document", "Note", etc., based on the content and format.
   - If uncertain, leave it as an empty string `""`.

7. **Production Method Extraction:**

   - Identify how the document was produced (e.g., "Handwritten", "Typewritten", "Printed", "Manuscript").
   - Look for clues in the OCR text quality, formatting, or explicit mentions of the production method.
   - If uncertain, leave it as an empty string `""`.

8. **Period Extraction:**

   - Extract the historical period, era, or time frame relevant to the letter's content or creation.
   - The period should be formatted as: "Period Name (date range)" where the date range is in parentheses.
   - Examples: "Post-Presidential Years (1913-January 6, 1919)", "Theodore Roosevelt and Public Memory (1919-present)", "Presidential Years (1901-1909)".
   - If no period can be determined, use an empty string `""`.

9. **Citation Extraction:**

   - Extract any citation information, reference numbers, catalog numbers, or archival identifiers mentioned in the text.
   - This may include collection names, box numbers, folder numbers, or other archival references.
   - If no citation information is present, use an empty string `""`.

10. **Rights Extraction:**

    - Identify copyright, usage rights, or access restrictions mentioned in the document.
    - This may include copyright notices, public domain indicators, or usage restrictions.
    - If no rights information is present, use an empty string `""`.

11. **Language Extraction:**

    - Identify the language of the document. For Theodore Roosevelt letters, this is typically "English".
    - Set the value to "English" unless the document is clearly in another language. If uncertain, leave it as an empty string `""`.

12. **Output Format Compliance:**
    - Populate the JSON structure below with the extracted metadata in the `extracted_metadata` object.
    - Provide an overall confidence score for the metadata extraction as a whole in the `metadata_extraction_confidence` field at the root level.
    - The confidence score should be a numerical value between 0.0 and 1.0, where:
      - 1.0 indicates very high confidence in all extracted fields
      - 0.5-0.9 indicates moderate confidence with some uncertainty
      - Below 0.5 indicates low confidence with significant uncertainty
    - Consider factors such as OCR text quality, clarity of information, completeness of data, and your certainty about each extracted field when determining the confidence score.

# Output Format

The output must strictly adhere to the following JSON schema:

```json
{
  "extracted_metadata": {
    "Title": "Letter regarding policy measures",
    "Description": "Letter discussing new policy measures and related matters.",
    "Creator": "Charles W. Fairbanks",
    "Recipient": "Roosevelt, Theodore",
    "Resource Type": "Letter",
    "Production Method": "Handwritten",
    "Creation Date": "1918-07-24",
    "Period": "",
    "Citation": "",
    "Rights": "",
    "Language": "English"
  },
  "metadata_extraction_confidence": 0.85
}
```

# Examples

**Input Letter Text:**

```
Oyster Bay, N.Y.
July 24, 1918

Dear Mr. Roosevelt,

Thank you for your letter regarding the new policy measures. I hope to discuss this further when we next meet.

Sincerely,
Charles W. Fairbanks
```

**Example Output:**

```json
{
  "extracted_metadata": {
    "Title": "Letter regarding new policy measures",
    "Description": "Letter discussing new policy measures and a request to meet for further discussion.",
    "Creator": "Charles W. Fairbanks",
    "Recipient": "Roosevelt, Theodore",
    "Resource Type": "Letter",
    "Production Method": "Handwritten",
    "Creation Date": "1918-07-24",
    "Period": "Post-Presidential Years (1913-January 6, 1919)",
    "Citation": "",
    "Rights": "",
    "Language": "English"
  },
  "metadata_extraction_confidence": 0.85
}
```

**Input Letter Text:**

```
January 3rd

Dear Mr. Roosevelt,

The enclosed letter discusses a matter of importance related to conservation in the national parks.

Best regards
```

**Example Output:**

```json
{
  "extracted_metadata": {
    "Title": "Letter regarding conservation in national parks",
    "Description": "Letter discussing a matter of importance related to conservation in the national parks.",
    "Creator": "",
    "Recipient": "Roosevelt, Theodore",
    "Resource Type": "Letter",
    "Production Method": "",
    "Creation Date": "",
    "Period": "",
    "Citation": "",
    "Rights": "",
    "Language": "English"
  },
  "metadata_extraction_confidence": 0.45
}
```

# Notes

- **Output Structure:** The output must have two root-level fields:
  - `extracted_metadata`: An object containing all the metadata fields (Title, Description, Creator, etc.)
  - `metadata_extraction_confidence`: A numerical value between 0.0 and 1.0 at the root level
- **REQUIRED FIELDS:** "Title" and "Description" are MANDATORY and must NEVER be empty strings. You must always provide values for these fields, even if you need to infer or generate them from the letter content.
- **Uncertainty Handling:** For all fields EXCEPT Title and Description: If you are uncertain about any field, leave it as an empty string `""`. Do not use placeholders like `(Untitled)` or `(Unknown)`. Only populate fields when you have reasonable confidence in the extracted value.
- **Language Field:** For Theodore Roosevelt letters, this is typically "English". Only set it to "English" if you are confident, otherwise leave it empty.
- **Date Format:** Always maintain the ISO date format (`YYYY-MM-DD`) for complete dates. If the date is incomplete or uncertain, leave it as an empty string rather than using partial formats.
- **Preserve Original Content:** Preserve names as they appear in the letter but normalize the date format for clarity in outputs.
- **All Fields Required:** All metadata fields must be included in the `extracted_metadata` object, even if their values are empty strings.
- **Description Field:** Should be a concise summary (1-3 sentences) of the letter's main content or purpose.
- **Confidence Score:** The `metadata_extraction_confidence` field should reflect your overall confidence in the metadata extraction quality, considering OCR text quality, clarity of information, completeness of data, and certainty about each extracted field.
