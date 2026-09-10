'use client'

import DOMPurify from 'dompurify'
import { Accordion, Button, Chip, InlineIcon } from '@/components/ui'
import styles from './BookDetail.module.css'

interface BookDetailProps {
  data: Record<string, unknown>
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

function extractYear(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw) return null
  const date = new Date(raw)
  if (isNaN(date.getTime())) {
    const match = raw.match(/\d{4}/)
    return match ? match[0] : null
  }
  return String(date.getUTCFullYear())
}

interface BookCitationFields {
  authors?: string
  title?: string
  publisher?: string
  year?: string | null
}

function buildBookCitations(f: BookCitationFields) {
  const author = f.authors || 'Unknown Author'
  const title = f.title || 'Untitled'
  const publisher = f.publisher || ''
  const year = f.year || 'n.d.'

  const chicago = [
    `${author}. `,
    `*${title}*.`,
    publisher ? ` ${publisher},` : '',
    ` ${year}.`,
  ].join('')

  const mla = [
    `${author}. `,
    `*${title}*.`,
    publisher ? ` ${publisher},` : '',
    ` ${year}.`,
  ].join('')

  const apa = [
    `${author} `,
    `(${year}). `,
    `*${title}*.`,
    publisher ? ` ${publisher}.` : '',
  ].join('')

  return { chicago, mla, apa }
}

export function BookDetail({ data }: BookDetailProps) {
  const date = formatDate(data.book_published_date)
  const year = extractYear(data.book_published_date)
  const title = (data.book_title as string) || (data.title as string) || ''
  const authors = data.book_authors as string | undefined

  const citations = buildBookCitations({
    authors,
    title,
    publisher: data.book_publisher as string,
    year,
  })

  const language = data.book_language as string | undefined
  const subjects = typeof data.book_subjects === 'string'
    ? data.book_subjects.split(/,\s*/).map(s => s.trim()).filter(Boolean)
    : []

  return (
    <div className={styles.container}>
      <div className={styles.detailHeader}>
        {date && <span className={styles.date}>{date}</span>}
        {title ? <h1 className={styles.title}>{title}</h1> : null}
        {authors && <span className={styles.authors}>{authors}</span>}
        <div className={styles.chipRow}>
          <Chip
            label="Book"
            className={styles.typeChip}
            tabIndex={-1}
          />
          {language && (
            <Chip
              label={language}
              className={styles.categoryChip}
              tabIndex={-1}
            />
          )}
          {subjects.map((subject) => (
            <Chip
              key={subject}
              label={subject}
              className={styles.categoryChip}
              tabIndex={-1}
            />
          ))}
        </div>
      </div>

      <div className={styles.detailColumn}>
        {data.book_description ? (
          <div className={styles.descriptionBlock}>
            <span className={styles.label}>Description</span>
            <div className={styles.description}>
              <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(data.book_description as string) }} />
            </div>
          </div>
        ) : null}

        {data.chapter_title ? (
          <div className={styles.fieldBlock}>
            <span className={styles.label}>Chapter</span>
            <p className={styles.fieldValue}>{data.chapter_title as string}</p>
          </div>
        ) : null}

        {data.book_publisher ? (
          <div className={styles.fieldBlock}>
            <span className={styles.label}>Publisher</span>
            <p className={styles.fieldValue}>{data.book_publisher as string}</p>
          </div>
        ) : null}

        {data.book_isbn ? (
          <div className={styles.fieldBlock}>
            <span className={styles.label}>ISBN</span>
            <p className={styles.fieldValue}>{data.book_isbn as string}</p>
          </div>
        ) : null}

        <Accordion.Root className={styles.accordionRoot} multiple>
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
                <p className={styles.accordionContent}>{citations.chicago}</p>
              </div>
              <div className={styles.citationBlock}>
                <span className={styles.citationFormat}>MLA</span>
                <p className={styles.accordionContent}>{citations.mla}</p>
              </div>
              <div className={styles.citationBlock}>
                <span className={styles.citationFormat}>APA</span>
                <p className={styles.accordionContent}>{citations.apa}</p>
              </div>
            </Accordion.Panel>
          </Accordion.Item>

          {data.text ? (
            <Accordion.Item value="excerpt" className={styles.accordionItem}>
              <Accordion.Header>
                <Accordion.Trigger className={styles.accordionTrigger}>
                  <InlineIcon name="plus" className={styles.accordionIconPlus} />
                  <InlineIcon name="minus" className={styles.accordionIconMinus} />
                  <span className={styles.accordionLabel}>View Excerpt</span>
                </Accordion.Trigger>
              </Accordion.Header>
              <Accordion.Panel className={styles.accordionPanel}>
                <p className={styles.accordionContent}>{data.text as string}</p>
              </Accordion.Panel>
            </Accordion.Item>
          ) : null}
        </Accordion.Root>

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
      </div>
    </div>
  )
}
