/** Core item stored in the knowledge base */
export interface Item {
  id: string;
  type: 'text' | 'file' | 'image' | 'code' | 'github_star' | 'github_external';
  title: string;
  content: string;
  tags: string[];
  summary: string;
  space: string;
  created_at: string;
  updated_at: string;
  file_path?: string;
  file_size?: number;
  mime_type?: string;
  has_file?: boolean;
  chunk_score?: number;
  match_chunk?: string;
  suggested_collections?: SuggestedCollection[];
  duplicate?: boolean;
  _gh_external?: GitHubExternal;
}

export interface SuggestedCollection {
  id: string;
  name: string;
  icon: string;
  similarity: number;
}

export interface GitHubExternal {
  full_name: string;
  html_url: string;
  stars: number;
  forks: number;
  language: string;
  topics: string[];
}

/** GitHub repo stored in the database */
export interface GitHubRepo {
  item_id: string;
  full_name: string;
  owner: string;
  repo_name: string;
  html_url: string;
  description: string;
  homepage: string;
  language: string;
  topics: string;
  license: string;
  stars: number;
  forks: number;
  watchers: number;
  open_issues: number;
  default_branch: string;
  pushed_at: string;
  created_at_gh: string;
  ai_summary: string;
  ai_tags: string;
  ai_platforms: string;
  category_id: string | null;
  last_synced: string;
  synced_by: string;
  title?: string;
  summary?: string;
  tags?: string;
  created_at?: string;
  content?: string;
  category_name?: string;
  category_color?: string;
  category_icon?: string;
}

/** GitHub release */
export interface GitHubRelease {
  id: string;
  item_id: string;
  full_name: string;
  tag_name: string;
  name: string;
  body: string;
  html_url: string;
  published_at: string;
  is_prerelease: boolean;
  is_read: boolean;
  assets: string | ReleaseAsset[];
  fetched_at?: string;
}

export interface ReleaseAsset {
  name: string;
  size: number;
  url: string;
  content_type: string;
}

/** GitHub account configured in the system */
export interface GitHubAccount {
  login: string;
  avatar_url: string;
  name: string;
  label: string;
  enabled: boolean;
  token_preview?: string;
}

/** Cross-references for an item */
export interface CrossRefs {
  github_repos: GitHubRepo[];
  related_items: Item[];
}

/** Collection */
export interface Collection {
  id: string;
  name: string;
  description: string;
  icon: string;
  created_at: string;
  updated_at: string;
  item_count: number;
}

/** Space */
export interface Space {
  id: string;
  name: string;
  context_prompt: string;
}

/** Digest/report config */
export interface DigestConfig {
  [key: string]: string | number | boolean;
  daily_enabled: boolean;
  daily_hour: number;
  weekly_enabled: boolean;
  weekly_hour: number;
  weekly_day: number;
  gh_stars_daily_enabled: boolean;
  gh_stars_daily_hour: number;
  gh_stars_weekly_enabled: boolean;
  gh_stars_weekly_hour: number;
  gh_stars_weekly_day: number;
  gh_trending_daily_enabled: boolean;
  gh_trending_daily_hour: number;
  gh_trending_weekly_enabled: boolean;
  gh_trending_weekly_hour: number;
  gh_trending_weekly_day: number;
  gh_trending_monthly_enabled: boolean;
  gh_trending_monthly_hour: number;
  gh_trending_monthly_day: number;
  last_daily: string;
  last_weekly: string;
  last_gh_stars_daily: string;
  last_gh_stars_weekly: string;
  last_gh_trending_daily: string;
  last_gh_trending_weekly: string;
  last_gh_trending_monthly: string;
}

/** GitHub category */
export interface GitHubCategory {
  id: string;
  name: string;
  keywords: string;
  color: string;
  icon: string;
  repo_count?: number;
}

/** Tag with count */
export interface TagStat {
  name: string;
  count: number;
}

/** Reminder */
export interface Reminder {
  id: string;
  to_user_id: string;
  context_token: string;
  content: string;
  remind_at: string;
  status: 'pending' | 'sent' | 'cancelled';
}

/** Subscription */
export interface Subscription {
  id: string;
  item_id: string;
  full_name: string;
  enabled: boolean;
}

/** Graph node and link for knowledge graph */
export interface GraphNode {
  id: string;
  name: string;
  group: string;
  val: number;
}

export interface GraphLink {
  source: string;
  target: string;
  value: number;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

/** Discover trending item */
export interface TrendingItem {
  full_name: string;
  html_url: string;
  description: string;
  language: string;
  stars: number;
  stargazers_count: number;
  forks: number;
  stars_today?: number;
  built_by?: string[];
  topics?: string[];
}

/** Stats */
export interface Stats {
  total: number;
  files: number;
  texts: number;
  codes: number;
  total_size: number;
}

/** API response for list endpoints */
export interface ListResponse<T> {
  items: T[];
  total: number;
  page?: number;
  page_size?: number;
}
