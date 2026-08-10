"use client";

import { Button, Select } from "@/components/ui";
import { number } from "@/lib/format";

/**
 * Offset pager for the `{items, total, limit, offset}` envelope every list endpoint returns.
 *
 * Tables previously rendered one fixed page and showed nothing about the rest, so anything
 * past the limit was invisible with no indication it existed.
 */
export function Pagination({
  total,
  limit,
  offset,
  onOffsetChange,
  onLimitChange,
  label = "rows",
}: {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
  onLimitChange?: (limit: number) => void;
  label?: string;
}) {
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + limit, total);
  const canPrev = offset > 0;
  const canNext = last < total;

  if (total === 0) return null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-3 py-2">
      <p className="tabular text-2xs text-muted-foreground">
        {number(first)}–{number(last)} of {number(total)} {label}
      </p>
      <div className="flex items-center gap-2">
        {onLimitChange && (
          <Select
            aria-label="Rows per page"
            className="h-7 text-2xs"
            value={limit}
            onChange={(event) => {
              onLimitChange(Number(event.target.value));
              onOffsetChange(0);
            }}
          >
            {[25, 50, 100].map((size) => (
              <option key={size} value={size}>
                {size} / page
              </option>
            ))}
          </Select>
        )}
        <Button
          variant="secondary"
          size="sm"
          disabled={!canPrev}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={!canNext}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
