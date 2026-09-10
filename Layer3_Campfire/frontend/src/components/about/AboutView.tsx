'use client'

import { useEffect } from 'react'
import { Accordion, InlineIcon } from '@/components/ui'
import { useAboutViewAnimation } from '@/hooks/useAboutViewAnimation'
import styles from './AboutView.module.css'

export function AboutView() {
  const { containerRef, headerRef, contentRef, backgroundOverlayRef, triggerLeaveAnimation } = useAboutViewAnimation()

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [])

  return (
    <main ref={containerRef} className={styles.page}>
      <header ref={headerRef} className={styles.header}>
        <button
          className={styles.headerBackButton}
          onClick={triggerLeaveAnimation}
          aria-label="Go back to home"
        >
          <InlineIcon name="arrow-left" size={40} />
          <span className={styles.headerBackLabel}>Back to Welcome</span>
        </button>
      </header>
      <div ref={contentRef} className={styles.content}>
        <h1 className={styles.title}>How we use artificial intelligence</h1>
        <div className={styles.heroImage} role="img" aria-label="Neutral historical artifact placeholder">
          TR
        </div>
        <div className={styles.heroDivider} />
        <div className={styles.layout}>
          {/* Desktop static TOC */}
          <nav className={styles.tocDesktop} aria-label="Table of contents">
            <h2 className={styles.tocTitle}>Contents</h2>
            <div className={styles.tocDivider} />
            <ul className={styles.tocList}>
              <li className={styles.tocListItem}>
                <a className={`${styles.tocLink} ${styles.tocLinkNumbered}`} href="#interactive-ai-agent">
                  1. Interactive AI Agent
                </a>
              </li>
              <li className={styles.tocListItem}>
                <a className={`${styles.tocLink} ${styles.tocLinkNumbered}`} href="#ai-assisted-cataloguing">
                  2. AI-assisted Cataloguing
                </a>
              </li>
              <li className={styles.tocListItem}>
                <a className={`${styles.tocLink} ${styles.tocLinkNumbered}`} href="#voice-technology">
                  3. Voice Technology
                </a>
              </li>
              <li className={styles.tocListItem}>
                <a className={styles.tocLink} href="#final-note">
                  A Final Note on Human-Centered Approach
                </a>
              </li>
            </ul>
            <div className={styles.tocDivider} />
          </nav>

          {/* Mobile/Tablet accordion TOC */}
          <nav className={styles.tocMobile} aria-label="Table of contents">
            <Accordion.Root className={styles.tocAccordion}>
              <Accordion.Item value="toc" className={styles.tocItem}>
                <Accordion.Header className={styles.tocHeader}>
                  <Accordion.Trigger className={styles.tocToggle}>
                    <span className={styles.tocTitle}>Contents</span>
                    <InlineIcon name="chevron-down" size={12} className={styles.tocChevron} />
                  </Accordion.Trigger>
                </Accordion.Header>
                <div className={styles.tocDivider} />
                <Accordion.Panel className={styles.tocPanel}>
                  <ul className={styles.tocList}>
                    <li className={styles.tocListItem}>
                      <a className={`${styles.tocLink} ${styles.tocLinkNumbered}`} href="#interactive-ai-agent">
                        1. Interactive AI Agent
                      </a>
                    </li>
                    <li className={styles.tocListItem}>
                      <a className={`${styles.tocLink} ${styles.tocLinkNumbered}`} href="#ai-assisted-cataloguing">
                        2. AI-assisted Cataloguing
                      </a>
                    </li>
                    <li className={styles.tocListItem}>
                      <a className={`${styles.tocLink} ${styles.tocLinkNumbered}`} href="#voice-technology">
                        3. Voice Technology
                      </a>
                    </li>
                    <li className={styles.tocListItem}>
                      <a className={styles.tocLink} href="#final-note">
                        A Final Note on Human-Centered Approach
                      </a>
                    </li>
                  </ul>
                  <div className={styles.tocDividerBottom} />
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion.Root>
          </nav>
          <div className={styles.body}>
            <p className={styles.intro}>
              AI is intentionally applied in the following three areas, each designed to enhance engagement without replacing curatorial expertise or historical context:
            </p>
            <div className={styles.sections}>
              <h2 className={styles.sectionTitle} id="interactive-ai-agent">
                1. Interactive AI Agent
              </h2>
              <p className={styles.bodyText}>
                An AI-powered agent helps visitors explore the Library’s collections and stories. The agent supports discovery by answering questions, guiding exploration, and helping visitors connect themes across hundreds of thousands of historical materials related to Theodore Roosevelt.
              </p>
              <p className={styles.bodyText}>
                In the background, the agent orchestrates a series of “agents”, or components that leverage large language models (LLMs) to process inputs and generate outputs.
              </p>
              <p className={styles.bodyText}>
                The experience is designed as a point of access—not an authority—encouraging curiosity while directing users back to primary sources, expert interpretation, and the Library’s broader collections.
              </p>
              <h2 className={styles.sectionTitle} id="ai-assisted-cataloguing">
                2. AI-assisted Cataloguing
              </h2>
              <p className={styles.bodyText}>
                AI is also used behind the scenes to assist with the processing of approximately 300,000 historical records. This includes technologies such as optical character recognition (OCR), used to generate transcripts of text records, and metadata generation, used to identify attributes such as the resource type (letter, telegram, photograph, post card, etc.). Both are powered by large language models (LLMs).
              </p>
              <p className={styles.bodyText}>
                These tools support archivists and researchers by accelerating routine processes and improving discoverability, while all final curatorial decisions remain guided by human expertise.
              </p>
              <h2 className={styles.sectionTitle} id="voice-technology">
                3. Voice Technology
              </h2>
              <p className={styles.bodyText}>
                For select experiences, including the Talk with TR interpretative experience in the physical exhibit, voice technology supports immersive storytelling. This includes a voice-cloning application, which uses audio materials from a voice actor in order to train a model that is then able to generate a synthetic voice that sounds like Theodore Roosevelt. Any use of voice technology is intentional and carefully reviewed, with clear safeguards, approvals, and contextual framing to ensure it is respectful, transparent, and aligned with the Library’s educational mission.
              </p>
              <h2 className={styles.sectionTitle} id="final-note">
                A Final Note on Human-Centered Approach
              </h2>
              <p className={styles.bodyText}>
                Across all uses, artificial intelligence is treated as a supporting tool—not a replacement for historians, educators, or curators. The goal is to responsibly extend access to history, reduce barriers to exploration, and create meaningful pathways for learning.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Background overlay for smooth transition to home page */}
      <div ref={backgroundOverlayRef} className={styles.backgroundOverlay} />
    </main>
  )
}
