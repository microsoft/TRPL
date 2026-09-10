# Synthetic home-gallery fixtures

The home gallery uses three fictional metadata records so contributors can run
frontend checks and inspect the static UI without archive content, external
storage, or third-party media.

`home-gallery-artifacts.json` is a checked-in derived projection of
`Layer1_Data_foundations/apps/functions/helper/content_source/data/fictional-content-source-pack.json`.
Every projected record:

- uses an ID prefixed with `synthetic-`;
- identifies one of the pack's invented institutions as its creator;
- uses an `example.org/synthetic-records/` URL;
- contains no image path, deployment identifier, or real collection claim.

The canonical pack stores neutral PNG generator parameters. Shared runtime code
generates the bytes used by the ingestion adapter and this data-URI projection;
no gallery images, downloaded binaries, SVG rasterizer, or remote media are
tracked or fetched.

## Schema

```typescript
interface GalleryArtifact {
  id: string
  title: string
  type: string
  date: string
  creator: string
  recordUrl: string
  category: string
  placeholderDataUri: string
}
```

Regenerate the projection through the canonical mapper when the pack changes.
Do not edit this projection independently.

## Runtime behavior

`HomeView` passes the generated data URIs to the existing `Gallery` component:

- desktop uses the two-column vertical infinite-scroll gallery;
- mobile and tablet use the two-row horizontal infinite-scroll gallery;
- gallery clicks navigate to the matching synthetic artifact route;
- no remote or tracked binary image is required.
