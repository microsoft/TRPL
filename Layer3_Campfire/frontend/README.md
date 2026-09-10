# Reading Room Frontend

Next.js 16 application for the Theodore Roosevelt Presidential Library Reading Room - an AI-powered research platform for exploring TR's life and legacy.

## Prerequisites

- [Bun](https://bun.sh/) runtime v1.3.14
- Node.js 20+ (for tooling compatibility)

## Getting Started

### Installation

1. **Install Bun** (if not already installed):
   
   **Windows (PowerShell):**
   ```powershell
   powershell -c "irm bun.sh/install.ps1|iex"
   ```
   
   **macOS/Linux:**
   ```bash
   curl -fsSL https://bun.sh/install | bash
   ```
   
   Restart your terminal/IDE after installation.

2. **Install dependencies:**
   ```bash
   bun install
   ```
   
   If you encounter permission errors on Windows:
   ```bash
   bun install --force
   ```

3. **Set up environment variables** (optional - only needed for backend integration):
   
   Copy `.env.example` to `.env.local` and fill in values when backend is ready.

4. **Review the synthetic home assets**:

   Follow [`public/data/GALLERY_ARTIFACTS.md`](public/data/GALLERY_ARTIFACTS.md)
   for the canonical fictional gallery projection. If a deployment adds a
   background or other image that must load before the home entrance animation,
   list its public path in `HOME_CRITICAL_ASSET_PATHS` in
   `src/lib/constants.ts`. Gallery images should not be listed there because
   the gallery manages their loading directly.

### Running the Development Server

```bash
bun dev
```

The application will be available at:
- Local: [http://localhost:3000](http://localhost:3000)
- Network: `http://[your-local-ip]:3000`

## Tech Stack

| Category | Technology |
|----------|------------|
| Framework | [Next.js 16](https://nextjs.org/) (App Router) |
| Language | [TypeScript](https://www.typescriptlang.org/) |
| Runtime | [Bun](https://bun.sh/) |
| UI Components | [Base UI](https://base-ui.com/) |
| Styling | CSS Modules |
| Animation | [GSAP](https://gsap.com/) |
| State | [Zustand](https://zustand-demo.pmnd.rs/) |
| Server State | [TanStack Query](https://tanstack.com/query) |
| Validation | [Zod](https://zod.dev/) |
| Testing | [Jest](https://jestjs.io/) + [React Testing Library](https://testing-library.com/) |

## Learn More

- `src/mockdata/` contains actions, topics, and prompt examples.
- `../../Layer1_Data_foundations/apps/functions/helper/content_source/data/fictional-content-source-pack.json`
  is the canonical fictional record/rights/content pack.
- `public/data/home-gallery-artifacts.json` is its verified gallery projection,
  including generated neutral PNG data URLs.

These files are the seed; no database or cloud bootstrap is used.

- [Next.js Documentation](https://nextjs.org/docs) - Framework features and API
