use crate::models::SearchResultItem;
use regex::Regex;
use std::time::Duration;

pub struct WebSearchRunner;

impl WebSearchRunner {
    pub async fn search(query: &str, max_results: usize) -> Vec<SearchResultItem> {
        let q = query.trim();
        if q.is_empty() {
            return Vec::new();
        }

        if let Ok(res) = Self::try_duckduckgo_lite(q, max_results).await {
            if !res.is_empty() {
                return res;
            }
        }

        if let Ok(res) = Self::try_duckduckgo_html(q, max_results).await {
            if !res.is_empty() {
                return res;
            }
        }

        Vec::new()
    }

    pub fn format_markdown(query: &str, results: &[SearchResultItem]) -> String {
        let mut md = format!("### 🔍 Autonomous Web Search Results for: `{}`\n\n", query);
        if results.is_empty() {
            md.push_str("No relevant search results found. Try refining keywords.\n");
            return md;
        }

        for (i, r) in results.iter().enumerate() {
            md.push_str(&format!(
                "{}. **[{}]({})**\n   {}\n\n",
                i + 1,
                if r.title.is_empty() { "Web Result" } else { &r.title },
                r.url,
                r.snippet
            ));
        }

        md
    }

    async fn try_duckduckgo_lite(query: &str, max_results: usize) -> Result<Vec<SearchResultItem>, reqwest::Error> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(12))
            .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            .build()?;

        let resp = client
            .post("https://lite.duckduckgo.com/lite/")
            .form(&[("q", query)])
            .send()
            .await?
            .text()
            .await?;

        Ok(Self::parse_lite_html(&resp, max_results))
    }

    async fn try_duckduckgo_html(query: &str, max_results: usize) -> Result<Vec<SearchResultItem>, reqwest::Error> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(12))
            .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            .build()?;

        let resp = client
            .get("https://html.duckduckgo.com/html/")
            .query(&[("q", query)])
            .send()
            .await?
            .text()
            .await?;

        Ok(Self::parse_lite_html(&resp, max_results))
    }

    fn parse_lite_html(html: &str, max_results: usize) -> Vec<SearchResultItem> {
        let link_re = Regex::new(r#"<a\b[^>]*\bclass=['"]result-link['"][^>]*>([\s\S]*?)</a>"#).unwrap();
        let snippet_re = Regex::new(r#"<td\b[^>]*class=['"]result-snippet['"][^>]*>([\s\S]*?)</td>"#).unwrap();
        let href_re = Regex::new(r#"href=['"]([^'"]+)['"]"#).unwrap();
        let uddg_re = Regex::new(r#"uddg=([^&]+)"#).unwrap();

        let link_matches: Vec<_> = link_re.captures_iter(html).collect();
        let snippet_matches: Vec<_> = snippet_re.captures_iter(html).collect();

        let mut list = Vec::new();
        let limit = max_results.min(link_matches.len());

        for i in 0..limit {
            let cap = &link_matches[i];
            let whole_tag = cap.get(0).map_or("", |m| m.as_str());
            let title_raw = cap.get(1).map_or("", |m| m.as_str());

            let mut link = String::new();
            if let Some(h_cap) = href_re.captures(whole_tag) {
                let raw_href = h_cap.get(1).map_or("", |m| m.as_str());
                link = if let Some(u_cap) = uddg_re.captures(raw_href) {
                    urlencoding::decode(u_cap.get(1).map_or("", |m| m.as_str()))
                        .unwrap_or_default()
                        .to_string()
                } else {
                    raw_href.to_string()
                };
            }

            let title = Self::clean_html(title_raw);
            let snippet = if i < snippet_matches.len() {
                Self::clean_html(snippet_matches[i].get(1).map_or("", |m| m.as_str()))
            } else {
                String::new()
            };

            if !title.is_empty() || !link.is_empty() {
                list.push(SearchResultItem {
                    title,
                    url: link,
                    snippet,
                });
            }
        }

        list
    }

    fn clean_html(input: &str) -> String {
        let tag_re = Regex::new(r#"<[^>]+>"#).unwrap();
        let stripped = tag_re.replace_all(input, "");
        stripped
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", "\"")
            .replace("&#39;", "'")
            .replace("&nbsp;", " ")
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
    }
}
