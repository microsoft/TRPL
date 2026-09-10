import { useCallback, useRef } from 'react';

/**
 * Serialize Cosmos-backed saves for one document so metadata/OCR/visual blurs
 * do not race with the same stale etag (false "updated elsewhere" conflicts).
 */
export function useDocumentSaveQueue() {
  const inFlightRef = useRef<Promise<unknown> | null>(null);

  const runDocumentSave = useCallback(async <T>(save: () => Promise<T>): Promise<T> => {
    if (inFlightRef.current) {
      try {
        await inFlightRef.current;
      } catch {
        // Prior save failed; still attempt this one.
      }
    }

    const task = save();
    inFlightRef.current = task;

    try {
      return await task;
    } finally {
      if (inFlightRef.current === task) {
        inFlightRef.current = null;
      }
    }
  }, []);

  return runDocumentSave;
}
