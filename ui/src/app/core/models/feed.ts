// Refleja hub/api/routers/feeds.py.
export interface FeedSummary {
  feed_id: string;
  destination_id: string;
  subtype: string;
  entries: number;
  // Ruta bajo la que nginx sirve este mismo archivo (ver nginx.conf
  // `location ^~ /feeds/`) -- mismo origen que la UI, asi que alcanza con
  // anteponer location.origin para armar el link completo a copiar.
  public_path: string;
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
