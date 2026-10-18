"use client";

import type { WorkspaceState } from "@/lib/types";
import { buttonStyles } from "./ui";

interface AnalysisStatusProps {
  state: Extract<WorkspaceState, "uploading" | "queued" | "analyzing" | "failed" | "canceled">;
  /** Real upload progress from the browser (0–1), only while uploading. */
  uploadProgress: number | null;
  error: { message: string; retryable: boolean } | null;
  cancelNote: string | null;
  onCancel: () => void;
  onRetry: () => void;
  onBack: () => void;
}

const copy = {
  uploading: { label: "Uploading", body: "Keep this tab open until the upload finishes." },
  queued: { label: "Queued", body: "The recording is uploaded and waiting for the analysis server." },
  analyzing: {
    label: "Analyzing",
    body: "Tracking people, hands and surfaces and looking for possible breaches. Longer videos take a few minutes.",
  },
  failed: { label: "Didn’t finish", body: "" },
  canceled: { label: "Canceled", body: "Your recording and details are still here." },
};

const tone = {
  uploading: { border: "border-accent", dot: "bg-accent", text: "text-accent" },
  queued: { border: "border-accent", dot: "bg-accent", text: "text-accent" },
  analyzing: { border: "border-accent", dot: "bg-accent", text: "text-accent" },
  failed: { border: "border-flag-mark", dot: "bg-flag-mark", text: "text-flag" },
  canceled: { border: "border-line-strong", dot: "bg-ink-faint", text: "text-ink-muted" },
};

export function AnalysisStatus({
  state,
  uploadProgress,
  error,
  cancelNote,
  onCancel,
  onRetry,
  onBack,
}: AnalysisStatusProps) {
  const busy = state === "uploading" || state === "queued" || state === "analyzing";
  const percent = uploadProgress == null ? null : Math.round(uploadProgress * 100);

  return (
    <section
      role="status"
      aria-live="polite"
      aria-label="Analysis status"
      className={`border-l-2 bg-surface px-4 py-3.5 ${tone[state].border}`}
    >
      <p className={`eyebrow flex items-center gap-2 ${tone[state].text}`}>
        <span className={`size-1.5 rounded-full ${tone[state].dot}`} aria-hidden="true" />
        {copy[state].label}
        {state === "uploading" && percent != null && <span className="tabular">{percent}%</span>}
      </p>
      <p className="mt-1.5 text-[15px]">
        {state === "failed" ? error?.message ?? "Something went wrong." : copy[state].body}
      </p>
      {state === "canceled" && cancelNote && <p className="mt-1 text-sm text-amber">{cancelNote}</p>}

      {busy && (
        <div className="mt-3 h-[3px] overflow-hidden bg-line" aria-hidden="true">
          {state === "uploading" && percent != null ? (
            <div className="h-full bg-accent transition-[width]" style={{ width: `${percent}%` }} />
          ) : (
            // No percentage exists for analysis, so show activity without pretending to measure it.
            <div className="indeterminate h-full w-2/5 bg-accent" />
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {busy && (
          <button type="button" className={buttonStyles.small} onClick={onCancel}>
            Cancel
          </button>
        )}
        {(state === "failed" || state === "canceled") && (
          <>
            {(state === "canceled" || error?.retryable !== false) && (
              <button type="button" className={buttonStyles.primary} onClick={onRetry}>
                {state === "canceled" ? "Start analysis again" : "Try again"}
              </button>
            )}
            <button type="button" className={buttonStyles.ghost} onClick={onBack}>
              Edit details
            </button>
          </>
        )}
      </div>
    </section>
  );
}
