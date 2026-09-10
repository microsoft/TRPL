'use client'

import { useState, useCallback } from 'react'
import { Accordion, Button, Chip, InlineIcon } from '@/components/ui'
import { isPdfUrl, toProxiedImageUrl } from '@/lib/utils'
import { PdfViewer } from './PdfViewer'
import { PeriodTimeline } from './PeriodTimeline'
import styles from './LetterDetail.module.css'

interface LetterDetailProps {
  data: Record<string, unknown>
}

function parseDate(raw: unknown): Date | null {
  if (typeof raw !== 'string' || !raw) return null
  const d = new Date(raw)
  return isNaN(d.getTime()) ? null : d
}

function formatDate(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw) return null
  const date = new Date(raw)
  if (isNaN(date.getTime())) return raw
  return date.toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  })
}

const MLA_MONTHS = [
  'Jan.', 'Feb.', 'Mar.', 'Apr.', 'May', 'June',
  'July', 'Aug.', 'Sept.', 'Oct.', 'Nov.', 'Dec.',
]

const FULL_MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function fmtChicagoDate(d: Date): string {
  return `${FULL_MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`
}

function fmtMlaDate(d: Date): string {
  return `${d.getUTCDate()} ${MLA_MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

function fmtApaDate(d: Date): string {
  return `${d.getUTCFullYear()}, ${FULL_MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`
}

function todayMla(): string {
  const d = new Date()
  return `${FULL_MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`
}

interface CitationFields {
  title?: string
  creator?: string
  date?: Date | null
  collection?: string
  resourceType?: string
  trcUrl?: string
}

function buildRecordCitations(f: CitationFields) {
  const t = f.title || 'Untitled'
  const col = f.collection ? ` ${f.collection}.` : ''

  const chicago = [
    `${t}.`,
    f.date ? ` [${fmtChicagoDate(f.date)}].` : '',
    col,
    f.trcUrl ? ` ${f.trcUrl}.` : '',
    ' Theodore Roosevelt Digital Library. Dickinson State University.',
  ].join('')

  const mla = [
    f.creator ? `${f.creator}. ` : '',
    `${t}.`,
    f.date ? ` [${fmtMlaDate(f.date)}].` : '',
    f.resourceType ? ` ${f.resourceType}.` : '',
    col,
    ' Theodore Roosevelt Digital Library. Dickinson State University.',
    ` ${todayMla()}.`,
    f.trcUrl ? ` ${f.trcUrl}.` : '',
  ].join('')

  const apa = [
    f.creator ? `${f.creator}.,` : '',
    f.date ? ` [${fmtApaDate(f.date)}].` : '',
    ` ${t}.`,
    col,
    ' Theodore Roosevelt Digital Library. Dickinson State University.',
    f.trcUrl ? ` Retrieved from ${f.trcUrl}.` : '',
  ].join('')

  return { chicago, mla, apa }
}

function buildCollectionCitations(collection: string) {
  const chicago = [
    `${collection}.`,
    ' Theodore Roosevelt Digital Library. Dickinson State University.',
  ].join('')

  const mla = [
    `${collection}.`,
    ' Theodore Roosevelt Digital Library. Dickinson State University.',
    ` ${todayMla()}.`,
  ].join('')

  const apa = [
    `${collection}.`,
    ' Theodore Roosevelt Digital Library. Dickinson State University.',
  ].join('')

  return { chicago, mla, apa }
}

export function LetterDetail({ data }: LetterDetailProps) {
  const urls = (data.trpl_file_url as string[]) || []
  const [pageIndex, setPageIndex] = useState(0)
  const [pdfPageCount, setPdfPageCount] = useState(0)
  const date = formatDate(data.creation_date)
  const parsedDate = parseDate(data.creation_date)
  const collection = (data.collection as string) || ''

  const recordCitations = buildRecordCitations({
    title: data.title as string,
    creator: data.creator as string,
    date: parsedDate,
    collection: collection || undefined,
    resourceType: data.resource_type as string,
    trcUrl: data.trc_url as string,
  })
  const collectionCitations = collection ? buildCollectionCitations(collection) : null

  const aiFields = ((data.ai_generated_fields as string[]) || []).map((f) => f.toLowerCase())
  const trcUrl = (data.trc_url as string) || null
  const isSinglePdf = urls.length === 1 && isPdfUrl(urls[0])

  const handlePageCountChange = useCallback((count: number) => {
    setPdfPageCount(count)
  }, [])

  const totalPages = isSinglePdf ? pdfPageCount : urls.length
  const showNav = totalPages > 1

  return (
    <div className={styles.container}>
      <div className={styles.detailHeader}>
        {date && <span className={styles.date}>{date}</span>}
        {data.title ? <h1 className={styles.title}>{data.title as string}</h1> : null}
        <Chip
          label={(data.resource_type as string) || 'Document'}
          className={styles.typeChip}
          tabIndex={-1}
        />
      </div>
      <div className={styles.imageColumn}>
        {urls.length > 0 && (
          <>
            {trcUrl ? (
              <a
                href={trcUrl}
                target="_blank"
                rel="noopener noreferrer"
                className={styles.imageLink}
              >
                {isSinglePdf ? (
                  <PdfViewer
                    url={urls[0]}
                    pageIndex={pageIndex}
                    onPageCountChange={handlePageCountChange}
                  />
                ) : (
                  <img
                    src={toProxiedImageUrl(urls[pageIndex])}
                    alt={`${(data.title as string) || 'Letter image'} — page ${pageIndex + 1}`}
                    className={styles.letterImage}
                  />
                )}
              </a>
            ) : (
              isSinglePdf ? (
                <PdfViewer
                  url={urls[0]}
                  pageIndex={pageIndex}
                  onPageCountChange={handlePageCountChange}
                />
              ) : (
                <img
                  src={toProxiedImageUrl(urls[pageIndex])}
                  alt={`${(data.title as string) || 'Letter image'} — page ${pageIndex + 1}`}
                  className={styles.letterImage}
                />
              )
            )}
            {showNav && (
              <div className={styles.imageNav}>
                <button
                  className={styles.imageNavButton}
                  onClick={() => setPageIndex((i) => i - 1)}
                  disabled={pageIndex === 0}
                  aria-label="Previous page"
                >
                  <svg width="18" height="12" viewBox="0 0 18 12" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" style={{ transform: 'scaleX(-1)' }}>
                    <path d="M11.454 1.24687C11.6275 1.07331 11.9089 1.07331 12.0825 1.24687L16.3047 5.4691C16.4783 5.64267 16.4783 5.92408 16.3047 6.0976L12.0825 10.3199C11.9089 10.4934 11.6275 10.4934 11.454 10.3199C11.2804 10.1463 11.2804 9.8649 11.454 9.6913L14.9175 6.2278L1.99046 6.2278C1.745 6.2278 1.54602 6.0288 1.54602 5.78337C1.54602 5.53791 1.745 5.33892 1.99046 5.33892L14.9175 5.33892L11.454 1.87542C11.2804 1.70185 11.2804 1.42044 11.454 1.24687Z" fill="currentColor"/>
                  </svg>
                </button>
                <span className={styles.imageNavCount}>
                  {pageIndex + 1} / {totalPages}
                </span>
                <button
                  className={styles.imageNavButton}
                  onClick={() => setPageIndex((i) => i + 1)}
                  disabled={pageIndex === totalPages - 1}
                  aria-label="Next page"
                >
                  <svg width="18" height="12" viewBox="0 0 18 12" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                    <path d="M11.454 1.24687C11.6275 1.07331 11.9089 1.07331 12.0825 1.24687L16.3047 5.4691C16.4783 5.64267 16.4783 5.92408 16.3047 6.0976L12.0825 10.3199C11.9089 10.4934 11.6275 10.4934 11.454 10.3199C11.2804 10.1463 11.2804 9.8649 11.454 9.6913L14.9175 6.2278L1.99046 6.2278C1.745 6.2278 1.54602 6.0288 1.54602 5.78337C1.54602 5.53791 1.745 5.33892 1.99046 5.33892L14.9175 5.33892L11.454 1.87542C11.2804 1.70185 11.2804 1.42044 11.454 1.24687Z" fill="currentColor"/>
                  </svg>
                </button>
              </div>
            )}
          </>
        )}
      </div>
      <div className={styles.detailColumn}>
        {data.trc_url ? (
          <Button
            variant="primary"
            href={data.trc_url as string}
            target="_blank"
            rel="noopener noreferrer"
            className={styles.sourceButton}
          >
            Original Source
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M3.61411 0.638899C3.61411 0.286051 3.90015 8.7253e-06 4.253 6.30559e-06L8.77064 5.27002e-06C9.12348 7.90793e-06 9.40953 0.28605 9.40953 0.638897L9.40952 5.15652C9.40952 5.50937 9.12347 5.79541 8.77062 5.79541C8.41778 5.79541 8.13173 5.50937 8.13173 5.15652L8.13175 2.18131L1.09066 9.22239C0.841162 9.47189 0.436647 9.47189 0.187145 9.22239C-0.0623565 8.97289 -0.0623698 8.56836 0.187132 8.31886L7.2282 1.27779L4.253 1.27779C3.90015 1.27779 3.61411 0.991744 3.61411 0.638899Z" fill="currentColor"/>
            </svg>
          </Button>
        ) : null}
        {data.period ? (
          <div className={styles.descriptionBlock}>
            <div className={styles.labelRow}>
              <span className={styles.label}>Period</span>
              {aiFields.includes('period') && <Chip label="AI Generated" tabIndex={-1} className={styles.aiBadge} />}
            </div>
            <p className={styles.description}>{data.period as string}</p>
            <PeriodTimeline period={data.period as string} />
          </div>
        ) : null}
        {data.description ? (
          <div className={styles.descriptionBlock}>
            <div className={styles.labelRow}>
              <span className={styles.label}>Description</span>
              {aiFields.includes('description') && <Chip label="AI Generated" tabIndex={-1} className={styles.aiBadge} />}
            </div>
            <p className={styles.description}>{data.description as string}</p>
          </div>
        ) : null}
        <div className={styles.fieldBlock}>
          <div className={styles.labelRow}>
            <span className={styles.label}>Collection</span>
            {aiFields.includes('collection') && <Chip label="AI Generated" tabIndex={-1} className={styles.aiBadge} />}
          </div>
          <p className={styles.fieldValue}>{(data.collection as string) || 'Unknown'}</p>
        </div>

        <Accordion.Root className={styles.accordionRoot} multiple>
          <Accordion.Item value="rights" className={styles.accordionItem}>
            <Accordion.Header>
              <Accordion.Trigger className={styles.accordionTrigger}>
                <InlineIcon name="plus" className={styles.accordionIconPlus} />
                <InlineIcon name="minus" className={styles.accordionIconMinus} />
                <span className={styles.accordionLabel}>Rights</span>
              </Accordion.Trigger>
            </Accordion.Header>
            <Accordion.Panel className={styles.accordionPanel}>
              <p className={styles.accordionContent}>
                These images are presented through a cooperative effort between the Library of Congress and Dickinson State University. See the{' '}
                <a href="https://www.theodorerooseveltcenter.org/about/terms-of-use/" target="_blank" rel="noopener noreferrer" className={styles.accordionLink}>Theodore Roosevelt Digital Library Terms of Use</a>
                {' '}for further copyright information.
              </p>
            </Accordion.Panel>
          </Accordion.Item>

          <Accordion.Item value="record-citation" className={styles.accordionItem}>
            <Accordion.Header>
              <Accordion.Trigger className={styles.accordionTrigger}>
                <InlineIcon name="plus" className={styles.accordionIconPlus} />
                <InlineIcon name="minus" className={styles.accordionIconMinus} />
                <span className={styles.accordionLabel}>Record Citation</span>
              </Accordion.Trigger>
            </Accordion.Header>
            <Accordion.Panel className={styles.accordionPanel}>
              <div className={styles.citationBlock}>
                <span className={styles.citationFormat}>Chicago</span>
                <p className={styles.accordionContent}>{recordCitations.chicago}</p>
              </div>
              <div className={styles.citationBlock}>
                <span className={styles.citationFormat}>MLA</span>
                <p className={styles.accordionContent}>{recordCitations.mla}</p>
              </div>
              <div className={styles.citationBlock}>
                <span className={styles.citationFormat}>APA</span>
                <p className={styles.accordionContent}>{recordCitations.apa}</p>
              </div>
            </Accordion.Panel>
          </Accordion.Item>

          {collectionCitations && (
            <Accordion.Item value="collection-citation" className={styles.accordionItem}>
              <Accordion.Header>
                <Accordion.Trigger className={styles.accordionTrigger}>
                  <InlineIcon name="plus" className={styles.accordionIconPlus} />
                  <InlineIcon name="minus" className={styles.accordionIconMinus} />
                  <span className={styles.accordionLabel}>Collection Citation</span>
                </Accordion.Trigger>
              </Accordion.Header>
              <Accordion.Panel className={styles.accordionPanel}>
                <div className={styles.citationBlock}>
                  <span className={styles.citationFormat}>Chicago</span>
                  <p className={styles.accordionContent}>{collectionCitations.chicago}</p>
                </div>
                <div className={styles.citationBlock}>
                  <span className={styles.citationFormat}>MLA</span>
                  <p className={styles.accordionContent}>{collectionCitations.mla}</p>
                </div>
                <div className={styles.citationBlock}>
                  <span className={styles.citationFormat}>APA</span>
                  <p className={styles.accordionContent}>{collectionCitations.apa}</p>
                </div>
              </Accordion.Panel>
            </Accordion.Item>
          )}

          {data.text ? (
            <Accordion.Item value="transcription" className={styles.accordionItem}>
              <Accordion.Header>
                <Accordion.Trigger className={styles.accordionTrigger}>
                  <InlineIcon name="plus" className={styles.accordionIconPlus} />
                  <InlineIcon name="minus" className={styles.accordionIconMinus} />
                  <span className={styles.accordionLabel}>View Transcription</span>
                </Accordion.Trigger>
              </Accordion.Header>
              <Accordion.Panel className={styles.accordionPanel}>
                <p className={styles.accordionContent}>{data.text as string}</p>
              </Accordion.Panel>
            </Accordion.Item>
          ) : null}
        </Accordion.Root>
      </div>
    </div>
  )
}
