import {
  GALLERY_ARTIFACTS,
  GALLERY_IMAGES,
  createGalleryImages,
  getHomeGalleryArtifactRoute,
  type GalleryArtifact,
} from '../constants'

describe('Home gallery configuration', () => {
  it('loads the small synthetic artifact pack', () => {
    expect(GALLERY_ARTIFACTS).toHaveLength(3)
    expect(GALLERY_IMAGES).toHaveLength(GALLERY_ARTIFACTS.length)
  })

  it('provides unique IDs and image paths', () => {
    const ids = GALLERY_IMAGES.map((image) => image.id)
    const paths = GALLERY_IMAGES.map((image) => image.src)

    expect(new Set(ids).size).toBe(ids.length)
    expect(new Set(paths).size).toBe(paths.length)
  })

  it('provides accessible text for every image', () => {
    GALLERY_IMAGES.forEach((image) => {
      expect(image.alt).not.toBe('')
      expect(image.caption).not.toBe('')
    })
  })

  it('uses complete fictional metadata and generated PNG data URIs', () => {
    const requiredFields = [
      'id',
      'title',
      'date',
      'category',
      'type',
      'creator',
      'recordUrl',
      'placeholderDataUri',
    ] as const
    const syntheticRecordPattern =
      /^https:\/\/example\.org\/synthetic-records\/[a-z0-9-]+$/

    GALLERY_ARTIFACTS.forEach((artifact) => {
      requiredFields.forEach((field) => expect(artifact[field]).toBeTruthy())
      expect(artifact.id).toMatch(/^synthetic-[a-z0-9-]+$/)
      expect(artifact.creator).toMatch(
        /^(Northwind Observatory Archive|Blue Lantern Civic Museum|Cedar Kite Institute)$/
      )
      expect(artifact.recordUrl).toMatch(syntheticRecordPattern)
      expect(artifact).not.toHaveProperty('imageUrl')
      expect(artifact.placeholderDataUri).toMatch(/^data:image\/png;base64,/)
    })
  })

  it('creates gallery images and navigation from supplied artifacts', () => {
    const artifacts: GalleryArtifact[] = [
      {
        id: 'artifact/one',
        title: 'Synthetic artifact',
        type: 'Document',
        date: '1901',
        creator: 'Example creator',
        recordUrl: 'https://example.invalid/artifacts/one',
        category: 'Example category',
        placeholderDataUri: 'data:image/png;base64,AAAA',
      },
    ]

    expect(createGalleryImages(artifacts)).toEqual([
      {
        id: 'artifact/one',
        src: 'data:image/png;base64,AAAA',
        alt: 'Synthetic artifact',
        caption: 'Synthetic artifact (1901)',
      },
    ])
    expect(getHomeGalleryArtifactRoute('artifact/one', artifacts)).toBe(
      '/artifact/artifact%2Fone?source=letter&from=home'
    )
  })
})
