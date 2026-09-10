import { useEffect, useState } from 'react';
import { apiService } from '../services/api';

type AuthenticatedBlobImageProps = {
  src: string;
  alt: string;
  className?: string;
  style?: React.CSSProperties;
  draggable?: boolean;
  onError?: () => void;
};

/**
 * Renders Azure Blob Storage images via the API proxy when storage is private (zero trust).
 */
export default function AuthenticatedBlobImage({
  src,
  alt,
  className,
  style,
  draggable,
  onError,
}: AuthenticatedBlobImageProps) {
  const [displaySrc, setDisplaySrc] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;

    (async () => {
      setDisplaySrc(null);
      try {
        objectUrl = await apiService.fetchBlobObjectUrl(src);
        if (!cancelled) {
          setDisplaySrc(objectUrl);
        }
      } catch {
        if (!cancelled) {
          onError?.();
        }
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [src, onError]);

  if (!displaySrc) {
    return null;
  }

  return (
    <img
      src={displaySrc}
      alt={alt}
      className={className}
      style={style}
      draggable={draggable}
      onError={onError}
    />
  );
}
