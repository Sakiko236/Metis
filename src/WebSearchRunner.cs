using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace Metis
{
    public static class WebSearchRunner
    {
        public static async Task<List<SearchResultItem>> SearchAsync(string query, CancellationToken token, int maxResults = 5)
        {
            if (string.IsNullOrWhiteSpace(query))
            {
                return new List<SearchResultItem>();
            }

            return await Task.Run(() =>
            {
                // Ensure TLS 1.2
                try
                {
                    ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12 | SecurityProtocolType.Tls11 | SecurityProtocolType.Tls;
                }
                catch { }

                // 1. Primary: DuckDuckGo Lite (lightweight, highly reliable, zero JS requirement)
                List<SearchResultItem> results = TryDuckDuckGoLite(query, maxResults, token);
                if (results != null && results.Count > 0)
                {
                    return results;
                }

                // 2. Secondary: DuckDuckGo HTML
                results = TryDuckDuckGoHtml(query, maxResults, token);
                if (results != null && results.Count > 0)
                {
                    return results;
                }

                return results ?? new List<SearchResultItem>();
            }, token);
        }

        private static List<SearchResultItem> TryDuckDuckGoLite(string query, int maxResults, CancellationToken token)
        {
            try
            {
                if (token.IsCancellationRequested) return null;

                string url = "https://lite.duckduckgo.com/lite/";
                string postData = "q=" + Uri.EscapeDataString(query.Trim());
                byte[] bytes = Encoding.UTF8.GetBytes(postData);

                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "POST";
                req.ContentType = "application/x-www-form-urlencoded";
                req.UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";
                req.Timeout = 12000;
                req.ReadWriteTimeout = 12000;

                using (Stream s = req.GetRequestStream())
                {
                    s.Write(bytes, 0, bytes.Length);
                }

                if (token.IsCancellationRequested) return null;

                string html;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    html = reader.ReadToEnd();
                }

                MatchCollection linkTags = Regex.Matches(html, @"<a\b[^>]*\bclass=['""]result-link['""][^>]*>([\s\S]*?)<\/a>", RegexOptions.IgnoreCase);
                MatchCollection snippetTags = Regex.Matches(html, @"<td\b[^>]*class=['""]result-snippet['""][^>]*>([\s\S]*?)<\/td>", RegexOptions.IgnoreCase);

                List<SearchResultItem> list = new List<SearchResultItem>();
                for (int i = 0; i < Math.Min(maxResults, linkTags.Count); i++)
                {
                    Match hm = Regex.Match(linkTags[i].Value, @"href=['""]([^'""]+)['""]", RegexOptions.IgnoreCase);
                    string rawHref = hm.Success ? hm.Groups[1].Value : "";
                    string link = rawHref;
                    Match uddg = Regex.Match(rawHref, @"uddg=([^&]+)");
                    if (uddg.Success)
                    {
                        link = Uri.UnescapeDataString(uddg.Groups[1].Value);
                    }

                    string title = CleanHtml(linkTags[i].Groups[1].Value);
                    string snippet = (i < snippetTags.Count) ? CleanHtml(snippetTags[i].Groups[1].Value) : "";

                    if (!string.IsNullOrEmpty(title) || !string.IsNullOrEmpty(link))
                    {
                        list.Add(new SearchResultItem
                        {
                            Title = title,
                            Url = link,
                            Snippet = snippet
                        });
                    }
                }
                return list;
            }
            catch
            {
                return null;
            }
        }

        private static List<SearchResultItem> TryDuckDuckGoHtml(string query, int maxResults, CancellationToken token)
        {
            try
            {
                if (token.IsCancellationRequested) return null;

                string url = "https://html.duckduckgo.com/html/?q=" + Uri.EscapeDataString(query.Trim());
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "GET";
                req.UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";
                req.Timeout = 12000;
                req.ReadWriteTimeout = 12000;

                string html;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (StreamReader reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    html = reader.ReadToEnd();
                }

                Regex blockRegex = new Regex(@"<div\s+class=""[^""]*result\s+results_links[^""]*""[^>]*>([\s\S]*?)<\/div>\s*<\/div>", RegexOptions.IgnoreCase);
                MatchCollection blocks = blockRegex.Matches(html);

                List<SearchResultItem> list = new List<SearchResultItem>();
                foreach (Match bm in blocks)
                {
                    string b = bm.Groups[1].Value;
                    Match tm = Regex.Match(b, @"<a\b[^>]*\bclass=""result__a""[^>]*\bhref=""([^""]+)""[^>]*>([\s\S]*?)<\/a>", RegexOptions.IgnoreCase);
                    if (!tm.Success)
                    {
                        tm = Regex.Match(b, @"<h2\b[^>]*>[\s\S]*?<a\b[^>]*\bhref=""([^""]+)""[^>]*>([\s\S]*?)<\/a>", RegexOptions.IgnoreCase);
                    }
                    if (!tm.Success) continue;

                    string rawHref = tm.Groups[1].Value;
                    string link = rawHref;
                    Match uddg = Regex.Match(rawHref, @"uddg=([^&]+)");
                    if (uddg.Success)
                    {
                        link = Uri.UnescapeDataString(uddg.Groups[1].Value);
                    }

                    string title = CleanHtml(tm.Groups[2].Value);
                    string snippet = "";
                    Match sm = Regex.Match(b, @"class=""result__snippet""[^>]*>([\s\S]*?)<\/(?:a|div|span)>", RegexOptions.IgnoreCase);
                    if (sm.Success)
                    {
                        snippet = CleanHtml(sm.Groups[1].Value);
                    }

                    list.Add(new SearchResultItem
                    {
                        Title = title,
                        Url = link,
                        Snippet = snippet
                    });

                    if (list.Count >= maxResults) break;
                }
                return list;
            }
            catch
            {
                return null;
            }
        }

        public static string FormatSearchResultsForPrompt(string query, List<SearchResultItem> results)
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendLine(string.Format("[Live Web Search Feedback - Query: \"{0}\"]", query));
            if (results == null || results.Count == 0)
            {
                sb.AppendLine("(No relevant web results found. Try refining keywords or specifying a more concrete library/API name.)");
                return sb.ToString();
            }

            for (int i = 0; i < results.Count; i++)
            {
                SearchResultItem item = results[i];
                sb.AppendLine(string.Format("{0}. Title: {1}", i + 1, item.Title));
                if (!string.IsNullOrEmpty(item.Url))
                {
                    sb.AppendLine(string.Format("   URL: {0}", item.Url));
                }
                if (!string.IsNullOrEmpty(item.Snippet))
                {
                    sb.AppendLine(string.Format("   Snippet: {0}", item.Snippet));
                }
            }
            sb.AppendLine();
            sb.AppendLine("Incorporate the web information above to surgically update the whitelisted files in-place or provide an accurate explanation.");
            return sb.ToString();
        }

        private static string CleanHtml(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return "";
            string clean = Regex.Replace(raw, @"<[^>]+>", "");
            return WebUtility.HtmlDecode(clean).Trim();
        }
    }
}
