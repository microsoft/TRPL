import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ExternalLink, Loader2, MessageSquareText, RefreshCw } from 'lucide-react';
import PageHeader from '@/components/PageHeader';
import ConfirmDialog from '@/components/ConfirmDialog';
import Toast from '@/components/Toast';
import {
  apiService,
  CorrectionRequestItem,
} from '@/services/api';

type StatusFilter = 'unread' | 'reviewed' | 'dismissed' | 'all';

type SortDir = 'newest' | 'oldest';

const NOTE_PREVIEW_LEN = 120;

/** Fixed page size (no user control). */
const PAGE_LIMIT = 10;

/** Review URL with repo/collection in path when API provides catalog metadata (correct breadcrumbs). */
function recordReviewPath(row: CorrectionRequestItem): string {
  const id = (row.record_id || '').trim();
  const repo = row.record_repository?.trim();
  const coll = row.record_collection?.trim();
  if (repo && coll && id) {
    return `/repositories/${encodeURIComponent(repo)}/collections/${encodeURIComponent(coll)}/review/${encodeURIComponent(id)}`;
  }
  return `/review/${encodeURIComponent(id)}`;
}

const CorrectionRequestsPage: React.FC = () => {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [sortDir, setSortDir] = useState<SortDir>('newest');
  const [pageOffset, setPageOffset] = useState(0);
  const [total, setTotal] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const [items, setItems] = useState<CorrectionRequestItem[]>([]);
  const [fetching, setFetching] = useState(true);
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [detailItem, setDetailItem] = useState<CorrectionRequestItem | null>(null);
  const [ackConfirmId, setAckConfirmId] = useState<string | null>(null);
  const [dismissModal, setDismissModal] = useState<{ id: string; note: string } | null>(null);

  const apiSort: 'asc' | 'desc' = sortDir === 'newest' ? 'desc' : 'asc';

  const load = useCallback(
    async (keepPreviousItems = false) => {
      try {
        setFetching(true);
        if (!keepPreviousItems) {
          setItems([]);
        }
        const res = await apiService.listCorrectionRequests({
          status: filter,
          limit: PAGE_LIMIT,
          offset: pageOffset,
          sort: apiSort,
        });
        setItems(res.items || []);
        setHasMore(Boolean(res.has_more));
        setTotal(typeof res.total === 'number' ? res.total : null);
      } catch (e) {
        console.error(e);
        setItems([]);
        setHasMore(false);
        setTotal(null);
        setToast({
          type: 'error',
          message: e instanceof Error ? e.message : 'Failed to load correction requests',
        });
      } finally {
        setFetching(false);
      }
    },
    [filter, pageOffset, apiSort]
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void load(true);
      }
    };

    const onWindowFocus = () => {
      void load(true);
    };

    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('focus', onWindowFocus);

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('focus', onWindowFocus);
    };
  }, [load]);

  const runAcknowledge = async (id: string) => {
    try {
      setBusyId(id);
      await apiService.patchCorrectionRequest(id, { status: 'reviewed' });
      setToast({ type: 'success', message: 'Acknowledged.' });
      await load(true);
    } catch (e) {
      setToast({
        type: 'error',
        message: e instanceof Error ? e.message : 'Update failed',
      });
    } finally {
      setBusyId(null);
      setAckConfirmId(null);
    }
  };

  const runDismiss = async () => {
    if (!dismissModal) return;
    const { id, note } = dismissModal;
    try {
      setBusyId(id);
      await apiService.patchCorrectionRequest(id, {
        status: 'dismissed',
        dismissal_note: note.trim() || undefined,
      });
      setToast({ type: 'success', message: 'Request dismissed.' });
      setDismissModal(null);
      await load(true);
    } catch (e) {
      setToast({
        type: 'error',
        message: e instanceof Error ? e.message : 'Update failed',
      });
    } finally {
      setBusyId(null);
    }
  };

  const formatTime = (iso: string) => {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  const previewNote = (text: string) => {
    const t = text || '';
    if (t.length <= NOTE_PREVIEW_LEN) return t;
    return `${t.slice(0, NOTE_PREVIEW_LEN)}…`;
  };

  const recordDisplay = (row: CorrectionRequestItem) => {
    const repo = row.record_repository?.trim();
    const col = row.record_collection?.trim();
    const title = row.record_title?.trim();
    const recordId = row.record_id;
    return {
      repository: repo,
      collection: col,
      title,
      recordId,
    };
  };

  const statusLabel = (s: CorrectionRequestItem['status']) => {
    if (s === 'reviewed') return 'Acknowledged';
    if (s === 'unread') return 'Unread';
    if (s === 'dismissed') return 'Dismissed';
    return s;
  };

  const filterLabel = (f: StatusFilter) =>
    f === 'unread'
      ? 'Unread'
      : f === 'reviewed'
        ? 'Acknowledge'
        : f === 'dismissed'
          ? 'Dismissed'
          : 'All';

  const onFilterChange = (next: StatusFilter) => {
    setFilter(next);
    setPageOffset(0);
  };

  const onSortChange = (next: SortDir) => {
    setSortDir(next);
    setPageOffset(0);
  };

  const pageStartIndex = total != null ? Math.min(pageOffset + 1, Math.max(total, 1)) : pageOffset + 1;
  const pageEndIndex =
    total != null ? Math.min(pageOffset + items.length, total) : pageOffset + items.length;

  const canPrev = pageOffset > 0;
  const canNext = hasMore;

  const paginationBar = (
    <div className="flex flex-wrap items-center justify-end gap-2 text-sm text-gray-600 mt-4 w-full">
      <button
        type="button"
        className="px-3 py-1.5 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-50"
        disabled={!canPrev || fetching}
        onClick={() => setPageOffset(Math.max(0, pageOffset - PAGE_LIMIT))}
      >
        Previous
      </button>
      <span className="tabular-nums px-2">
        {total != null ? (
          <>
            {pageStartIndex}–{pageEndIndex} of {total}
          </>
        ) : (
          <>Page offset {pageOffset}</>
        )}
      </span>
      <button
        type="button"
        className="px-3 py-1.5 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-50"
        disabled={!canNext || fetching}
        onClick={() => setPageOffset(pageOffset + PAGE_LIMIT)}
      >
        Next
      </button>
    </div>
  );

  const showFullPageSpinner = fetching && items.length === 0;
  const showTableOverlay = fetching && items.length > 0;
  const showDismissalColumn = filter === 'dismissed' || filter === 'all';

  return (
    <div className="flex flex-col min-h-full bg-gray-50">
      <PageHeader
        title="Correction requests"
        subtitle="Incoming notes from registered downstream applications"
      />
      <div className="flex-1 p-6 max-w-8xl mx-auto w-full">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <label className="text-sm text-gray-600 flex items-center gap-2">
            Status
            <select
              value={filter}
              onChange={(e) => onFilterChange(e.target.value as StatusFilter)}
              className="border border-gray-200 rounded-lg px-2 py-1 text-sm bg-white min-w-[12rem]"
            >
              <option value="all">{filterLabel('all')}</option>
              <option value="unread">{filterLabel('unread')}</option>
              <option value="reviewed">{filterLabel('reviewed')}</option>
              <option value="dismissed">{filterLabel('dismissed')}</option>
            </select>
          </label>

          <label className="text-sm text-gray-600 flex items-center gap-2">
            Sort by date
            <select
              value={sortDir}
              onChange={(e) => onSortChange(e.target.value as SortDir)}
              className="border border-gray-200 rounded-lg px-2 py-1 text-sm bg-white"
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </label>

          <button
            type="button"
            onClick={() => void load(true)}
            disabled={fetching}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <RefreshCw className={`h-4 w-4 shrink-0 ${fetching ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>

        {showFullPageSpinner ? (
          <div className="flex justify-center py-16 text-museum-accent">
            <Loader2 className="w-10 h-10 animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <>
            <div className="bg-white rounded-lg border border-gray-200 p-12 text-center text-gray-600">
              <MessageSquareText className="w-12 h-12 mx-auto mb-3 text-gray-400" />
              <p className="text-lg font-medium text-gray-800">No correction requests</p>
              <p className="mt-1 text-sm">
                {filter === 'unread'
                  ? 'There are no unread requests.'
                  : `No items for the “${filterLabel(filter)}” filter.`}
              </p>
            </div>
          </>
        ) : (
          <div className="relative bg-white rounded-lg border border-gray-200 overflow-hidden shadow-sm">
            {showTableOverlay && (
              <div
                className="absolute inset-0 z-10 flex items-center justify-center bg-white/75 backdrop-blur-[1px]"
                aria-busy="true"
                aria-label="Loading"
              >
                <Loader2 className="w-10 h-10 animate-spin text-museum-accent" />
              </div>
            )}
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm table-fixed text-left">
                <thead className="bg-gray-100 text-left text-gray-700">
                  <tr>
                    <th className="px-4 py-3 font-semibold w-56 text-left border-l border-gray-100">
                      Repository / collection
                    </th>
                    <th className="px-4 py-3 font-semibold w-44 text-left border-l border-gray-100">Source</th>
                    <th className="px-4 py-3 font-semibold text-left border-l border-gray-100">Note</th>
                    {showDismissalColumn && (
                      <th className="px-4 py-3 font-semibold text-left border-l border-gray-100 min-w-[8rem]">
                        Dismissal note
                      </th>
                    )}
                    <th className="px-4 py-3 font-semibold w-28 border-l border-gray-100">Status</th>
                    <th className="px-4 py-3 font-semibold w-44">Received</th>
                    <th className="px-4 py-3 font-semibold text-center">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {items.map((row) => (
                    <tr
                      key={row.id}
                      onClick={() => navigate(recordReviewPath(row))}
                      className={`cursor-pointer hover:bg-gray-50/80 ${
                        row.status === 'unread' ? 'bg-amber-50/60' : ''
                      }`}
                    >
                      <td className="px-4 py-3 align-top text-left border-l border-gray-100">
                        {(() => {
                          const d = recordDisplay(row);
                          const notInCatalog = row.record_available === false;
                          if (notInCatalog) {
                            return (
                              <>
                                <div className="text-gray-900 font-medium leading-snug break-words line-clamp-3" title={row.record_id}>
                                  —
                                </div>
                                <div className="text-gray-700 text-xs leading-snug break-words line-clamp-3" />
                                <div className="mt-0.5 font-mono text-[11px] text-gray-500 break-all line-clamp-2">
                                  {d.recordId}
                                </div>
                              </>
                            );
                          }
                          return (
                            <>
                              <div
                                className="text-gray-900 font-medium leading-snug break-words line-clamp-3"
                                title={[d.repository || d.title || '—', d.collection].filter(Boolean).join(' · ')}
                              >
                                {d.repository || d.title || '—'}
                              </div>
                              <div className="text-gray-700 text-xs leading-snug break-words line-clamp-3">
                                {d.collection || ''}
                              </div>
                            </>
                          );
                        })()}
                      </td>
                      <td className="px-4 py-3 text-gray-800 align-top text-left border-l border-gray-100">
                        <div className="break-words">{row.source || row.source_display_name}</div>
                        <div className="text-xs text-gray-500 break-all">{row.source_app_id}</div>
                      </td>
                      <td className="px-4 py-3 text-gray-800 align-top text-left border-l border-gray-100">
                        <p className="whitespace-pre-wrap break-words line-clamp-3">{previewNote(row.note || '')}</p>
                        <button
                          type="button"
                          className="mt-1 text-xs font-medium text-museum-accent hover:underline"
                          onClick={(e) => {
                            e.stopPropagation();
                            setDetailItem(row);
                          }}
                        >
                          View
                        </button>
                      </td>
                      {showDismissalColumn && (
                        <td className="px-4 py-3 text-gray-800 align-top text-left border-l border-gray-100">
                          <p
                            className="whitespace-pre-wrap break-words text-sm leading-snug line-clamp-4 text-gray-800"
                            title={
                              row.status === 'dismissed' && row.dismissal_note?.trim()
                                ? row.dismissal_note
                                : undefined
                            }
                          >
                            {row.status === 'dismissed' && row.dismissal_note?.trim()
                              ? row.dismissal_note
                              : '—'}
                          </p>
                        </td>
                      )}
                      <td className="px-4 py-3 text-gray-700 align-top border-l border-gray-100">{statusLabel(row.status)}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-gray-600 align-top">{formatTime(row.received_at || row.timestamp)}</td>
                      <td className="px-4 py-3 align-top border-l border-gray-100">
                        <div className="flex flex-col items-center justify-center gap-1.5 text-center">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigate(recordReviewPath(row));
                            }}
                            className="inline-flex items-center justify-center gap-1 text-museum-accent hover:underline text-xs font-medium"
                          >
                            <ExternalLink className="w-3.5 h-3.5 shrink-0" />
                            Open record
                          </button>
                          {row.status === 'unread' && (
                            <>
                              <button
                                type="button"
                                disabled={busyId === row.id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setAckConfirmId(row.id);
                                }}
                                className="text-xs font-medium text-green-700 hover:underline disabled:opacity-50"
                              >
                                Acknowledge
                              </button>
                              <button
                                type="button"
                                disabled={busyId === row.id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setDismissModal({ id: row.id, note: '' });
                                }}
                                className="text-xs font-medium text-gray-600 hover:underline disabled:opacity-50"
                              >
                                Dismiss
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {paginationBar}
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={ackConfirmId !== null}
        onClose={() => {
          if (busyId !== ackConfirmId) setAckConfirmId(null);
        }}
        onConfirm={() => ackConfirmId && void runAcknowledge(ackConfirmId)}
        title="Acknowledge correction request"
        message="Mark this request as acknowledged?"
        confirmText="Acknowledge"
        cancelText="Cancel"
        variant="info"
        isLoading={ackConfirmId !== null && busyId === ackConfirmId}
      />

      {dismissModal && (
        <div
          className="fixed inset-0 z-50 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-labelledby="dismiss-cr-title"
          onKeyDown={(e) => {
            if (e.key === 'Escape' && !busyId) setDismissModal(null);
          }}
        >
          <div
            className="fixed inset-0 bg-gray-500/75 backdrop-blur-sm"
            aria-hidden="true"
            onClick={() => !busyId && setDismissModal(null)}
          />
          <div className="flex min-h-full items-center justify-center p-4">
            <div
              className="relative w-full max-w-lg rounded-xl bg-white shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="px-5 py-4 border-b border-gray-200">
                <h2 id="dismiss-cr-title" className="text-lg font-semibold text-gray-900">
                  Dismiss correction request
                </h2>
                <p className="mt-1 text-sm text-gray-600">
                  This will mark the request as dismissed. You can add an optional note for the record.
                </p>
              </div>
              <div className="px-5 py-4">
                <label htmlFor="dismiss-note" className="block text-sm font-medium text-gray-700 mb-1">
                  Optional dismissal note
                </label>
                <textarea
                  id="dismiss-note"
                  rows={3}
                  value={dismissModal.note}
                  onChange={(e) => setDismissModal({ ...dismissModal, note: e.target.value })}
                  disabled={busyId === dismissModal.id}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-900 focus:ring-2 focus:ring-museum-accent/30 focus:border-museum-accent disabled:opacity-60"
                  placeholder="Leave empty if not needed"
                />
              </div>
              <div className="bg-gray-50 px-5 py-4 flex flex-col-reverse sm:flex-row sm:justify-end gap-2 rounded-b-xl">
                <button
                  type="button"
                  disabled={busyId === dismissModal.id}
                  onClick={() => setDismissModal(null)}
                  className="px-4 py-2.5 rounded-lg bg-white text-sm font-semibold text-gray-900 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={busyId === dismissModal.id}
                  onClick={() => void runDismiss()}
                  className="px-4 py-2.5 rounded-lg bg-gray-800 text-sm font-semibold text-white hover:bg-gray-900 disabled:opacity-50"
                >
                  {busyId === dismissModal.id ? 'Dismissing…' : 'Dismiss'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {detailItem && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40"
          role="dialog"
          aria-modal="true"
          aria-labelledby="cr-detail-title"
        >
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[85vh] overflow-hidden flex flex-col">
            <div className="px-5 py-4 border-b border-gray-200 flex justify-between items-center">
              <h2 id="cr-detail-title" className="text-lg font-semibold text-gray-900">
                Correction note
              </h2>
              <button
                type="button"
                className="text-gray-500 hover:text-gray-800 text-base font-medium"
                onClick={() => setDetailItem(null)}
              >
                Close
              </button>
            </div>
            <div className="p-6 overflow-y-auto space-y-8">
              <div>
                <p className="text-lg sm:text-xl text-gray-900 leading-relaxed whitespace-pre-wrap break-words">
                  <span className="font-semibold text-gray-800">Title :-</span>{' '}
                  <span className="font-semibold text-gray-900">
                    {detailItem.record_available === false
                      ? '—'
                      : detailItem.record_title?.trim() || '—'}
                  </span>
                </p>
              </div>

              <div>
                <p className="text-base font-semibold text-gray-700 mb-3">Message</p>
                <div className="bg-amber-50 border border-amber-100 rounded-xl p-6 min-h-[5rem]">
                  <p className="whitespace-pre-wrap break-words text-gray-900 text-lg leading-relaxed">
                    {detailItem.note || ''}
                  </p>
                </div>
              </div>

              {detailItem.status === 'dismissed' && (
                <div>
                  <p className="text-base font-semibold text-gray-700 mb-3">Dismissal note</p>
                  <div className="bg-gray-50 border border-gray-200 rounded-xl p-6 min-h-[3rem]">
                    <p className="whitespace-pre-wrap break-words text-gray-900 text-base leading-relaxed">
                      {detailItem.dismissal_note?.trim() || '—'}
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {toast && (
        <Toast
          type={toast.type}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
};

export default CorrectionRequestsPage;
