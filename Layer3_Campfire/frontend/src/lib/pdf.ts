// eslint-disable-next-line @typescript-eslint/no-explicit-any
let cachedPdfJs: any = null

export async function loadPdfJs() {
  if (cachedPdfJs) return cachedPdfJs
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const pdfjs = (await import('pdfjs-dist/build/pdf.min.mjs')) as any
  pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'
  cachedPdfJs = pdfjs
  return pdfjs
}
