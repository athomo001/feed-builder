// Refleja hub/api/routers/feeds.py.
export interface FeedSummary {
  feed_id: string;
  destination_id: string;
  subtype: string;
  entries: number;
}

export interface FeedPreview {
  feed_id: string;
  preview: string[];
}

export interface FeedRebuildResult {
  feed_id: string;
  written: number;
  skipped_capacity: number;
}
