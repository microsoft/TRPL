/**
 * Application constants
 * Static values that don't change between environments
 */

import artifactsData from '../../public/data/home-gallery-artifacts.json'

// API endpoints (relative to Next.js server)
export const API_ROUTES = {
  CHAT: '/api/chat',
  SEARCH: '/api/search',
  SESSION_END: '/api/session/end',
  ARTIFACTS: '/api/artifacts',
} as const

// Modes for the mode selector
export const MODES = ['Discover', 'Research', 'Teach', 'Study'] as const

export type Mode = (typeof MODES)[number]

// Chat page mode options
export const CHAT_MODE_OPTIONS = [
  { label: 'Discovery Mode', value: 'discovery', icon: 'D', description: 'Curious about T.R.? Start here for stories, surprises, and a good place to begin.' },
  { label: 'Research Mode', value: 'research', icon: 'R', description: 'Deeper dives with sources, citations, and the scholarly detail researchers need.' },
  { label: 'For Teachers', value: 'teachers', icon: 'T', description: 'Lesson ideas, classroom activities, and primary sources aligned to your students.' },
  { label: 'For Students', value: 'students', icon: 'S', description: 'Clear answers, fun facts, and homework help — built for learners of all ages.' },
] as const

export type ChatMode = (typeof CHAT_MODE_OPTIONS)[number]['value']

// Suggested follow-up prompts in chat
export const CHAT_SUGGESTED_PROMPTS = [
  'What were his key accomplishments?',
  'Tell me about his conservation efforts',
  'Describe his Rough Riders experience',
  'What was his "Square Deal" policy?',
  'How did he become president?',
] as const

// Default initial query when no prompt is composed
export const DEFAULT_INITIAL_QUERY = 'Tell me about Theodore Roosevelt'

// Timing constants (milliseconds)
export const TIMING = {
  /** Typewriter animation speed (ms per character) */
  TYPEWRITER_SPEED: 5,
  /** Characters per tick when catching up after stream ends (3x normal speed) */
  TYPEWRITER_CATCHUP_CHARS: 3,
  /** Loading indicator dot animation interval */
  LOADING_DOT_INTERVAL: 400,
  /** Threshold for "user scrolled up" detection (px from bottom) */
  SCROLL_UP_THRESHOLD: 100,
} as const

// UI component defaults
export const UI = {
  /** Default number of suggested prompts visible before "View More" */
  SUGGESTED_PROMPTS_VISIBLE: 3,
  /** Default number of columns in gallery */
  GALLERY_COLUMNS: 2,
} as const

// Gallery animation and layout constants
export const GALLERY = {
  /** Default scroll speed in pixels per second */
  DEFAULT_SCROLL_SPEED: 30,
  /** Default number of columns in vertical gallery */
  DEFAULT_COLUMNS: 2,
  /** Estimated height of a single image for SSR calculations */
  ESTIMATED_IMAGE_HEIGHT: 120,
  /** SSR fallback viewport height */
  SSR_VIEWPORT_HEIGHT: 1080,
  /** Default number of duplicate sets for infinite scroll */
  DEFAULT_DUPLICATE_SETS: 2,
  /** Number of image set duplications for horizontal gallery (3x for fast scroll buffer) */
  HORIZONTAL_DUPLICATE_SETS: 3,
  /** Maximum duplicate sets to prevent excessive DOM nodes */
  MAX_DUPLICATE_SETS: 5,
  /** Desktop breakpoint for gallery variant switching */
  DESKTOP_BREAKPOINT: 1200,
  /** Gap size between gallery items (matches --space-md) */
  GAP_SIZE: 16,
  /** Default caption text when image has no caption */
  DEFAULT_CAPTION: 'View Details',
} as const

// Gallery animation physics constants
export const GALLERY_ANIMATION = {
  /** Minimum pixels of movement before drag is recognized */
  DRAG_THRESHOLD_PX: 5,
  /** Debounce delay for resize observer recalculations (ms) */
  RESIZE_DEBOUNCE_MS: 100,
  /** Fraction of accumulated delta to apply per frame (0.15 = 15% per frame) */
  WHEEL_SMOOTHING_FACTOR: 0.15,
  /** Stop rAF loop when accumulated delta falls below this threshold (pixels) */
  WHEEL_DELTA_THRESHOLD: 0.5,
  /** Threshold for determining if elements are in same column/row (px) */
  AXIS_DETECTION_THRESHOLD: 50,
} as const

// Page transition animation constants
export const PAGE_ANIMATION = {
  /** Default GSAP ease for most animations */
  EASE_DEFAULT: 'power2.out',
  /** GSAP ease for entrance animations (fast start, slow end) */
  EASE_ENTRANCE: 'power3.out',
  /** GSAP ease for exit animations */
  EASE_EXIT: 'power2.in',
  /** GSAP ease for bidirectional animations */
  EASE_INOUT: 'power2.inOut',

  // Home page entrance animation
  HOME: {
    /** Tagline typewriter duration */
    TAGLINE_DURATION: 0.6,
    /** Title typewriter duration */
    TITLE_DURATION: 0.75,
    /** Content slide-in duration */
    CONTENT_DURATION: 0.6,
    /** Gallery fade-in duration */
    GALLERY_DURATION: 1,
    /** Footer buttons duration */
    FOOTER_DURATION: 0.5,
    /** Footer buttons stagger delay */
    FOOTER_STAGGER: 0.1,
    /** Content initial Y offset (px) */
    CONTENT_OFFSET_Y: 30,
    /** Footer initial Y offset (px) */
    FOOTER_OFFSET_Y: 20,
  },

  // Home to chat transition animation
  TRANSITION: {
    /** Background overlay fade duration */
    BACKGROUND_DURATION: 0.6,
    /** Hero slide up duration */
    HERO_DURATION: 0.5,
    /** Hero Y offset (px) */
    HERO_OFFSET_Y: -150,
    /** Content slide up duration */
    CONTENT_DURATION: 0.5,
    /** Content Y offset (px) */
    CONTENT_OFFSET_Y: -100,
    /** Footer slide up duration */
    FOOTER_DURATION: 0.4,
    /** Footer Y offset (px) */
    FOOTER_OFFSET_Y: -80,
    /** Gallery fade out duration */
    GALLERY_DURATION: 0.4,
    /** Safety timeout before forcing navigation (ms) */
    SAFETY_TIMEOUT: 2000,
  },

  // Chat view entrance animation
  CHAT: {
    /** All entrance animation durations */
    ENTRANCE_DURATION: 0.6,
    /** Back button entrance fade duration */
    BACK_BUTTON_ENTRANCE_DURATION: 0.4,
    /** Chat area initial Y offset (px) */
    CHAT_AREA_OFFSET_Y: 200,
    /** Chatbar initial Y offset (px) */
    CHATBAR_OFFSET_Y: 200,
  },

  // Artifacts sidebar animation
  SIDEBAR: {
    /** Slide in duration */
    SLIDE_DURATION: 0.6,
    /** Toggle button fade duration */
    TOGGLE_FADE_DURATION: 0.3,
    /** Width visible when sidebar is closed (px) - shows ripped edge and covers button */
    CLOSED_VISIBLE_WIDTH: 130,
  },

  // In-app navigation animation (simple fade-in + slide-up when returning from another page)
  IN_APP: {
    /** Animation duration for all elements */
    DURATION: 0.6,
    /** Y offset for slide-up (px) */
    OFFSET_Y: 200,
  },
} as const

// Gallery projection derived from the canonical fictional content-source pack.
export interface GalleryArtifact {
  id: string
  title: string
  type: string
  date: string
  creator: string
  recordUrl: string
  category: string
  placeholderDataUri: string
}

const isGalleryArtifact = (value: unknown): value is GalleryArtifact => {
  if (!value || typeof value !== 'object') return false

  const artifact = value as Record<string, unknown>
  return [
    'id',
    'title',
    'type',
    'date',
    'creator',
    'recordUrl',
    'category',
    'placeholderDataUri',
  ].every((field) => typeof artifact[field] === 'string' && artifact[field].trim() !== '')
}

const manifestArtifacts: unknown[] = Array.isArray(artifactsData.artifacts)
  ? artifactsData.artifacts
  : []

if (!manifestArtifacts.every(isGalleryArtifact)) {
  throw new Error('Every home gallery artifact must provide the documented string fields')
}

export const GALLERY_ARTIFACTS: readonly GalleryArtifact[] = manifestArtifacts

// Add only images that must finish loading before the home entrance animation.
// Do not add gallery images; their components already manage image loading.
export const HOME_CRITICAL_ASSET_PATHS: readonly string[] = []

export const createGalleryImages = (
  artifacts: readonly GalleryArtifact[]
) =>
  artifacts.map((artifact) => ({
    id: artifact.id,
    src: artifact.placeholderDataUri,
    alt: artifact.title,
    caption: `${artifact.title} (${artifact.date})`,
  }))

export const GALLERY_IMAGES = createGalleryImages(GALLERY_ARTIFACTS)

export const getHomeGalleryArtifactRoute = (
  imageId: string,
  artifacts: readonly GalleryArtifact[] = GALLERY_ARTIFACTS
): string | null => {
  const artifact = artifacts.find(({ id }) => id === imageId)
  return artifact
    ? `/artifact/${encodeURIComponent(artifact.id)}?source=letter&from=home`
    : null
}
