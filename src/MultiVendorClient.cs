using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace Metis
{
    public static class MultiVendorClient
    {
        static MultiVendorClient()
        {
            try
            {
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12 | (SecurityProtocolType)3072 | (SecurityProtocolType)12288;
                ServicePointManager.DefaultConnectionLimit = 20;
            }
            catch
            {
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            }
        }

        public static async Task<string> SendChatAsync(
            VendorProfile vendor,
            string systemPrompt,
            string filesSnapshotBlock,
            IList<ChatHistoryItem> history,
            IList<WhitelistedFile> whitelist,
            CancellationToken cancellationToken,
            Action<string> onStreamingDisplayUpdate = null,
            string thinkingIntensity = "medium")
        {
            if (vendor == null)
            {
                throw new Exception("No valid model vendor configuration selected.");
            }

            if (vendor.Type == VendorType.LocalSandbox)
            {
                return await RunLocalSandboxEngineAsync(filesSnapshotBlock, history, whitelist, cancellationToken, onStreamingDisplayUpdate);
            }

            string baseUrl = SanitizeBaseUrl(vendor.BaseUrl);
            bool isLocalUrl = baseUrl.Contains("127.0.0.1") || baseUrl.Contains("localhost") || baseUrl.Contains("0.0.0.0");

            if (vendor.Type != VendorType.Ollama && !isLocalUrl && string.IsNullOrWhiteSpace(vendor.ApiKey))
            {
                throw new Exception(string.Format(
                    "Model vendor [{0}] is missing an API Key.\r\nPlease open Settings to configure your API key, or switch to Ollama (Local Models).",
                    vendor.DisplayName));
            }

            string fullSystem = systemPrompt + "\r\n\r\n" + filesSnapshotBlock;
            IList<ChatHistoryItem> effectiveHistory = BuildEffectiveHistoryWithXmlReminder(history, whitelist);
            List<WhitelistedFile> mediaFiles = ExtractMediaAttachments(whitelist);

            bool looksLikeOpenAIEndpoint =
                baseUrl.IndexOf("/v1", StringComparison.OrdinalIgnoreCase) >= 0 ||
                baseUrl.IndexOf("/chat/completions", StringComparison.OrdinalIgnoreCase) >= 0 ||
                baseUrl.EndsWith("/openai", StringComparison.OrdinalIgnoreCase);

            Func<Task<string>> dispatch = async () =>
            {
                if (vendor.Type == VendorType.Gemini && !looksLikeOpenAIEndpoint)
                {
                    return await CallGeminiNativeAsync(vendor, fullSystem, effectiveHistory, mediaFiles, cancellationToken, onStreamingDisplayUpdate, thinkingIntensity);
                }
                else if (vendor.Type == VendorType.Claude && !looksLikeOpenAIEndpoint)
                {
                    return await CallAnthropicClaudeAsync(vendor, fullSystem, effectiveHistory, mediaFiles, cancellationToken, onStreamingDisplayUpdate, thinkingIntensity);
                }
                else
                {
                    return await CallOpenAICompatibleAsync(vendor, fullSystem, effectiveHistory, mediaFiles, cancellationToken, onStreamingDisplayUpdate, thinkingIntensity);
                }
            };

            // HTTP 503 (service unavailable): exponential backoff (2s, 4s, 8s), retry at most 3 times, then give up.
            const int maxRetries503 = 3;
            int attempt = 0;
            while (true)
            {
                Exception failure = null;
                try
                {
                    return await dispatch();
                }
                catch (Exception ex)
                {
                    if (ex is OperationCanceledException || !Is503Error(ex.Message))
                    {
                        throw;
                    }
                    failure = ex;
                }

                if (attempt >= maxRetries503)
                {
                    throw new Exception(string.Format("Service unavailable (HTTP 503). Retried {0} times with exponential backoff and failed. Ended current interaction.\r\n{1}", attempt, failure.Message));
                }

                attempt++;
                int delaySeconds = (int)Math.Pow(2, attempt); // 2s, 4s, 8s
                if (onStreamingDisplayUpdate != null)
                {
                    try
                    {
                        onStreamingDisplayUpdate(string.Format("⚠️ Service returned HTTP 503, retrying in {0}s (attempt {1}/{2}, exponential backoff)...", delaySeconds, attempt, maxRetries503));
                    }
                    catch { }
                }
                await Task.Delay(TimeSpan.FromSeconds(delaySeconds), cancellationToken);
            }
        }

        private static bool Is503Error(string message)
        {
            return !string.IsNullOrEmpty(message) &&
                   System.Text.RegularExpressions.Regex.IsMatch(message, @"HTTP\s*503\b");
        }

        private static List<WhitelistedFile> ExtractMediaAttachments(IList<WhitelistedFile> whitelist)
        {
            List<WhitelistedFile> list = new List<WhitelistedFile>();
            if (whitelist == null) return list;
            foreach (WhitelistedFile wf in whitelist)
            {
                if (wf != null && !string.IsNullOrEmpty(wf.FullPath) && File.Exists(wf.FullPath))
                {
                    if (wf.IsBinaryMedia || MediaAttachmentHelper.IsMediaAttachment(wf.FullPath))
                    {
                        list.Add(wf);
                    }
                }
            }
            return list;
        }

        private static IList<ChatHistoryItem> BuildEffectiveHistoryWithXmlReminder(
            IList<ChatHistoryItem> history,
            IList<WhitelistedFile> whitelist)
        {
            // The complete, strict system instructions are already centralized in SystemPrompt and SnapshotBlock.
            return history;
        }

        private static string SanitizeBaseUrl(string url)
        {
            if (string.IsNullOrWhiteSpace(url)) return "";
            string trimmed = url.Trim().TrimEnd('/');
            // Fix accidental double port like :8080:8080
            trimmed = Regex.Replace(trimmed, @":(\d+):\1\b", ":$1");
            return trimmed;
        }

        public static async Task<List<string>> FetchOllamaModelsAsync(string baseUrl)
        {
            List<string> models = new List<string>();
            try
            {
                string root = string.IsNullOrWhiteSpace(baseUrl) ? "http://localhost:11434" : SanitizeBaseUrl(baseUrl);
                if (root.EndsWith("/v1", StringComparison.OrdinalIgnoreCase))
                {
                    root = root.Substring(0, root.Length - 3);
                }
                string url = root + "/api/tags";

                string resp = await HttpGetAsync(url, 4000);
                JavaScriptSerializer ser = new JavaScriptSerializer();
                Dictionary<string, object> rootObj = ser.Deserialize<Dictionary<string, object>>(resp);
                if (rootObj != null && rootObj.ContainsKey("models"))
                {
                    IList list = rootObj["models"] as IList;
                    if (list != null)
                    {
                        foreach (object item in list)
                        {
                            Dictionary<string, object> dict = item as Dictionary<string, object>;
                            if (dict != null && dict.ContainsKey("name") && dict["name"] != null)
                            {
                                models.Add(dict["name"].ToString());
                            }
                        }
                    }
                }
            }
            catch { }
            return models;
        }

        private static async Task<string> CallGeminiNativeAsync(
            VendorProfile vendor,
            string fullSystem,
            IList<ChatHistoryItem> history,
            IList<WhitelistedFile> mediaFiles,
            CancellationToken cancellationToken,
            Action<string> onStreamingDisplayUpdate,
            string thinkingIntensity)
        {
            string baseUrl = string.IsNullOrWhiteSpace(vendor.BaseUrl)
                ? "https://generativelanguage.googleapis.com/v1beta"
                : SanitizeBaseUrl(vendor.BaseUrl);
            string model = string.IsNullOrWhiteSpace(vendor.SelectedModel) ? "gemini-3.8-flash" : vendor.SelectedModel.Trim();
            if (model.StartsWith("models/")) model = model.Substring("models/".Length);

            string url = string.Format("{0}/models/{1}:generateContent?key={2}", baseUrl, model, Uri.EscapeDataString((vendor.ApiKey ?? "").Trim()));

            List<object> contents = new List<object>();
            for (int i = 0; i < history.Count; i++)
            {
                ChatHistoryItem msg = history[i];
                string geminiRole = string.Equals(msg.Role, "assistant", StringComparison.OrdinalIgnoreCase) ? "model" : "user";
                List<object> partsList = new List<object>();

                Dictionary<string, object> textPart = new Dictionary<string, object>();
                textPart["text"] = msg.Content ?? "";
                partsList.Add(textPart);

                // Attach multimodal inline_data (PDFs & Images) to the latest user message
                if (i == history.Count - 1 && geminiRole == "user" && mediaFiles != null && mediaFiles.Count > 0)
                {
                    foreach (WhitelistedFile mf in mediaFiles)
                    {
                        string mimeType;
                        string b64 = MediaAttachmentHelper.GetBase64ForApi(mf.FullPath, out mimeType);
                        if (!string.IsNullOrEmpty(b64))
                        {
                            Dictionary<string, object> inlineData = new Dictionary<string, object>();
                            inlineData["mime_type"] = mimeType;
                            inlineData["data"] = b64;
                            Dictionary<string, object> mediaPart = new Dictionary<string, object>();
                            mediaPart["inline_data"] = inlineData;
                            partsList.Add(mediaPart);
                        }
                    }
                }

                Dictionary<string, object> cItem = new Dictionary<string, object>();
                cItem["role"] = geminiRole;
                cItem["parts"] = partsList;
                contents.Add(cItem);
            }

            Dictionary<string, object> sysPart = new Dictionary<string, object>();
            sysPart["text"] = fullSystem;
            Dictionary<string, object> sysInst = new Dictionary<string, object>();
            sysInst["parts"] = new object[] { sysPart };

            Dictionary<string, object> genConfig = new Dictionary<string, object>();
            genConfig["temperature"] = 0.1;

            if (!string.IsNullOrEmpty(thinkingIntensity))
            {
                int budget = 4096;
                string lower = thinkingIntensity.Trim().ToLowerInvariant();
                switch (lower)
                {
                    case "off":
                        budget = 0;
                        break;
                    case "low":
                        budget = 1024;
                        break;
                    case "medium":
                        budget = 4096;
                        break;
                    case "high":
                        budget = 16384;
                        break;
                }
                Dictionary<string, object> thinkingConfig = new Dictionary<string, object>();
                thinkingConfig["thinkingBudget"] = budget;
                genConfig["thinkingConfig"] = thinkingConfig;
            }

            Dictionary<string, object> payload = new Dictionary<string, object>();
            payload["systemInstruction"] = sysInst;
            payload["contents"] = contents;
            payload["generationConfig"] = genConfig;

            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = int.MaxValue;
            string jsonBody = ser.Serialize(payload);

            string responseJson = await HttpPostJsonAsync(url, jsonBody, null, cancellationToken);
            Dictionary<string, object> root = ser.Deserialize<Dictionary<string, object>>(responseJson);

            if (root != null && root.ContainsKey("candidates"))
            {
                IList candidates = root["candidates"] as IList;
                if (candidates != null && candidates.Count > 0)
                {
                    Dictionary<string, object> cand0 = candidates[0] as Dictionary<string, object>;
                    if (cand0 != null && cand0.ContainsKey("content"))
                    {
                        Dictionary<string, object> contentObj = cand0["content"] as Dictionary<string, object>;
                        if (contentObj != null && contentObj.ContainsKey("parts"))
                        {
                            IList parts = contentObj["parts"] as IList;
                            if (parts != null)
                            {
                                StringBuilder sb = new StringBuilder();
                                foreach (object p in parts)
                                {
                                    Dictionary<string, object> pd = p as Dictionary<string, object>;
                                    if (pd != null)
                                    {
                                        if (pd.ContainsKey("thought") && pd["thought"] is bool && (bool)pd["thought"])
                                        {
                                            continue;
                                        }
                                        if (pd.ContainsKey("text") && pd["text"] != null)
                                        {
                                            sb.Append(pd["text"].ToString());
                                        }
                                    }
                                }
                                string finalOut = sb.ToString();
                                if (onStreamingDisplayUpdate != null)
                                {
                                    onStreamingDisplayUpdate(WhitelistManager.FormatModelTextForDisplay(finalOut));
                                }
                                return finalOut;
                            }
                        }
                    }
                }
            }

            throw new Exception("Gemini response parsing failed: " + responseJson);
        }

        private static async Task<string> CallAnthropicClaudeAsync(
            VendorProfile vendor,
            string fullSystem,
            IList<ChatHistoryItem> history,
            IList<WhitelistedFile> mediaFiles,
            CancellationToken cancellationToken,
            Action<string> onStreamingDisplayUpdate,
            string thinkingIntensity)
        {
            string baseUrl = string.IsNullOrWhiteSpace(vendor.BaseUrl)
                ? "https://api.anthropic.com/v1"
                : SanitizeBaseUrl(vendor.BaseUrl);
            string url = baseUrl.EndsWith("/messages", StringComparison.OrdinalIgnoreCase)
                ? baseUrl
                : baseUrl + "/messages";

            string model = string.IsNullOrWhiteSpace(vendor.SelectedModel) ? "claude-sonnet-5-5" : vendor.SelectedModel.Trim();

            List<object> messages = new List<object>();
            for (int i = 0; i < history.Count; i++)
            {
                ChatHistoryItem msg = history[i];
                string role = string.Equals(msg.Role, "assistant", StringComparison.OrdinalIgnoreCase) ? "assistant" : "user";
                Dictionary<string, object> m = new Dictionary<string, object>();
                m["role"] = role;

                if (i == history.Count - 1 && role == "user" && mediaFiles != null && mediaFiles.Count > 0)
                {
                    List<object> blocks = new List<object>();
                    foreach (WhitelistedFile mf in mediaFiles)
                    {
                        string mimeType;
                        string b64 = MediaAttachmentHelper.GetBase64ForApi(mf.FullPath, out mimeType);
                        if (string.IsNullOrEmpty(b64)) continue;

                        Dictionary<string, object> src = new Dictionary<string, object>();
                        src["type"] = "base64";
                        src["media_type"] = mimeType;
                        src["data"] = b64;

                        Dictionary<string, object> b = new Dictionary<string, object>();
                        b["type"] = MediaAttachmentHelper.IsPdfFile(mf.FullPath) ? "document" : "image";
                        b["source"] = src;
                        blocks.Add(b);
                    }

                    Dictionary<string, object> textBlock = new Dictionary<string, object>();
                    textBlock["type"] = "text";
                    textBlock["text"] = msg.Content ?? "";
                    blocks.Add(textBlock);

                    m["content"] = blocks;
                }
                else
                {
                    m["content"] = msg.Content ?? "";
                }

                messages.Add(m);
            }

            Dictionary<string, object> payload = new Dictionary<string, object>();
            payload["model"] = model;
            int maxTokens = 8192;

            if (!string.IsNullOrEmpty(thinkingIntensity))
            {
                string lower = thinkingIntensity.Trim().ToLowerInvariant();
                if (lower != "off")
                {
                    int budgetTokens = 4000;
                    if (lower == "low") budgetTokens = 1024;
                    else if (lower == "medium") budgetTokens = 4000;
                    else if (lower == "high") budgetTokens = 16000;

                    Dictionary<string, object> thinking = new Dictionary<string, object>();
                    thinking["type"] = "enabled";
                    thinking["budget_tokens"] = budgetTokens;
                    payload["thinking"] = thinking;
                    maxTokens = budgetTokens + 8192;
                }
            }

            payload["max_tokens"] = maxTokens;
            payload["system"] = fullSystem;
            payload["messages"] = messages;

            Dictionary<string, string> headers = new Dictionary<string, string>();
            headers["x-api-key"] = (vendor.ApiKey ?? "").Trim();
            headers["anthropic-version"] = "2023-06-01";

            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = int.MaxValue;
            string jsonBody = ser.Serialize(payload);

            string responseJson = await HttpPostJsonAsync(url, jsonBody, headers, cancellationToken);
            Dictionary<string, object> root = ser.Deserialize<Dictionary<string, object>>(responseJson);

            if (root != null && root.ContainsKey("content"))
            {
                IList contentBlocks = root["content"] as IList;
                if (contentBlocks != null)
                {
                    StringBuilder sb = new StringBuilder();
                    foreach (object block in contentBlocks)
                    {
                        Dictionary<string, object> bd = block as Dictionary<string, object>;
                        if (bd != null)
                        {
                            string bType = bd.ContainsKey("type") && bd["type"] != null ? bd["type"].ToString() : "";
                            if (string.Equals(bType, "thinking", StringComparison.OrdinalIgnoreCase))
                            {
                                continue;
                            }
                            if (bd.ContainsKey("text") && bd["text"] != null)
                            {
                                sb.Append(bd["text"].ToString());
                            }
                        }
                    }
                    string finalOut = sb.ToString();
                    if (onStreamingDisplayUpdate != null)
                    {
                        onStreamingDisplayUpdate(WhitelistManager.FormatModelTextForDisplay(finalOut));
                    }
                    return finalOut;
                }
            }

            throw new Exception("Anthropic Claude response parsing failed: " + responseJson);
        }

        private static async Task<string> CallOpenAICompatibleAsync(
            VendorProfile vendor,
            string fullSystem,
            IList<ChatHistoryItem> history,
            IList<WhitelistedFile> mediaFiles,
            CancellationToken cancellationToken,
            Action<string> onStreamingDisplayUpdate,
            string thinkingIntensity)
        {
            string baseUrl = string.IsNullOrWhiteSpace(vendor.BaseUrl)
                ? "https://api.openai.com/v1"
                : SanitizeBaseUrl(vendor.BaseUrl);

            string url = baseUrl;
            if (!url.EndsWith("/chat/completions", StringComparison.OrdinalIgnoreCase))
            {
                url = url + "/chat/completions";
            }

            string defaultModel = "gpt-6.1-sol";
            if (vendor.Type == VendorType.DeepSeek) defaultModel = "deepseek-flash";
            else if (vendor.Type == VendorType.Ollama) defaultModel = "qwen3-coder:30b";
            string model = string.IsNullOrWhiteSpace(vendor.SelectedModel) ? defaultModel : vendor.SelectedModel.Trim();

            Dictionary<string, string> headers = new Dictionary<string, string>();
            if (!string.IsNullOrWhiteSpace(vendor.ApiKey))
            {
                headers["Authorization"] = "Bearer " + vendor.ApiKey.Trim();
            }

            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = int.MaxValue;

            // Check if there are image or PDF multimodal blocks to send
            List<object> multimodalParts = new List<object>();
            bool isOfficialOpenAI = url.IndexOf("api.openai.com", StringComparison.OrdinalIgnoreCase) >= 0;

            if (mediaFiles != null && mediaFiles.Count > 0)
            {
                foreach (WhitelistedFile mf in mediaFiles)
                {
                    if (MediaAttachmentHelper.IsImageFile(mf.FullPath))
                    {
                        string mimeType;
                        string b64 = MediaAttachmentHelper.GetBase64ForApi(mf.FullPath, out mimeType);
                        if (!string.IsNullOrEmpty(b64))
                        {
                            Dictionary<string, object> imgUrlObj = new Dictionary<string, object>();
                            imgUrlObj["url"] = "data:" + mimeType + ";base64," + b64;
                            Dictionary<string, object> imgPart = new Dictionary<string, object>();
                            imgPart["type"] = "image_url";
                            imgPart["image_url"] = imgUrlObj;
                            multimodalParts.Add(imgPart);
                        }
                    }
                    else if (MediaAttachmentHelper.IsPdfFile(mf.FullPath) && isOfficialOpenAI)
                    {
                        string mimeType;
                        string b64 = MediaAttachmentHelper.GetBase64ForApi(mf.FullPath, out mimeType);
                        if (!string.IsNullOrEmpty(b64))
                        {
                            Dictionary<string, object> fileObj = new Dictionary<string, object>();
                            fileObj["filename"] = mf.FileName;
                            fileObj["file_data"] = "data:application/pdf;base64," + b64;
                            Dictionary<string, object> filePart = new Dictionary<string, object>();
                            filePart["type"] = "file";
                            filePart["file"] = fileObj;
                            multimodalParts.Add(filePart);
                        }
                    }
                }
            }

            if (multimodalParts.Count > 0)
            {
                try
                {
                    string mmJsonBody = BuildOpenAIPayloadJson(ser, model, fullSystem, history, multimodalParts, thinkingIntensity);
                    return await HttpPostOpenAIStreamOrJsonAsync(url, mmJsonBody, headers, cancellationToken, onStreamingDisplayUpdate);
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch
                {
                    // Automatic graceful fallback: if the OpenAI-compatible server is running a text-only model
                    // that rejects multimodal content arrays, retry with plain text.
                }
            }

            string plainJsonBody = BuildOpenAIPayloadJson(ser, model, fullSystem, history, null, thinkingIntensity);
            return await HttpPostOpenAIStreamOrJsonAsync(url, plainJsonBody, headers, cancellationToken, onStreamingDisplayUpdate);
        }

        private static string BuildOpenAIPayloadJson(
            JavaScriptSerializer ser,
            string model,
            string fullSystem,
            IList<ChatHistoryItem> history,
            List<object> multimodalParts,
            string thinkingIntensity = null)
        {
            List<object> messages = new List<object>();
            Dictionary<string, object> sysMsg = new Dictionary<string, object>();
            sysMsg["role"] = "system";
            sysMsg["content"] = fullSystem;
            messages.Add(sysMsg);

            for (int i = 0; i < history.Count; i++)
            {
                ChatHistoryItem msg = history[i];
                string role = string.Equals(msg.Role, "assistant", StringComparison.OrdinalIgnoreCase) ? "assistant" : "user";
                Dictionary<string, object> m = new Dictionary<string, object>();
                m["role"] = role;

                if (i == history.Count - 1 && role == "user" && multimodalParts != null && multimodalParts.Count > 0)
                {
                    List<object> contentArr = new List<object>();
                    Dictionary<string, object> textPart = new Dictionary<string, object>();
                    textPart["type"] = "text";
                    textPart["text"] = msg.Content ?? "";
                    contentArr.Add(textPart);
                    contentArr.AddRange(multimodalParts);
                    m["content"] = contentArr;
                }
                else
                {
                    m["content"] = msg.Content ?? "";
                }
                messages.Add(m);
            }

            Dictionary<string, object> payload = new Dictionary<string, object>();
            payload["model"] = model;
            payload["messages"] = messages;
            payload["stream"] = true;
            if (!model.StartsWith("o1") && !model.StartsWith("o3") && !model.Contains("reasoner"))
            {
                payload["temperature"] = 0.1;
            }

            if (!string.IsNullOrEmpty(thinkingIntensity))
            {
                string lower = thinkingIntensity.Trim().ToLowerInvariant();
                if (lower != "off")
                {
                    if (model.StartsWith("o1") || model.StartsWith("o3") || model.StartsWith("o4") || model.Contains("reasoner") || model.Contains("r1") || model.Contains("gpt-oss"))
                    {
                        payload["reasoning_effort"] = lower;
                    }
                }
            }

            return ser.Serialize(payload);
        }

        private static async Task<string> HttpPostOpenAIStreamOrJsonAsync(
            string url,
            string jsonBody,
            Dictionary<string, string> headers,
            CancellationToken cancellationToken,
            Action<string> onStreamingDisplayUpdate)
        {
            return await Task.Run(() =>
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "POST";
                req.ContentType = "application/json; charset=utf-8";
                req.Accept = "text/event-stream, application/json";
                req.Timeout = 180000;
                req.ReadWriteTimeout = 180000;

                if (headers != null)
                {
                    foreach (KeyValuePair<string, string> kv in headers)
                    {
                        req.Headers[kv.Key] = kv.Value;
                    }
                }

                byte[] bodyBytes = Encoding.UTF8.GetBytes(jsonBody);
                req.ContentLength = bodyBytes.Length;

                using (CancellationTokenRegistration reg = cancellationToken.Register(() =>
                {
                    try { req.Abort(); } catch { }
                }))
                {
                    try
                    {
                        using (Stream reqStream = req.GetRequestStream())
                        {
                            reqStream.Write(bodyBytes, 0, bodyBytes.Length);
                        }

                        using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                        using (Stream respStream = resp.GetResponseStream())
                        using (StreamReader reader = new StreamReader(respStream, Encoding.UTF8))
                        {
                            JavaScriptSerializer ser = new JavaScriptSerializer();
                            ser.MaxJsonLength = int.MaxValue;

                            StringBuilder contentBuilder = new StringBuilder();
                            StringBuilder rawFallbackBuilder = new StringBuilder();
                            bool isSseStream = false;
                            bool sawReasoningTokens = false;
                            Stopwatch uiThrottle = Stopwatch.StartNew();

                            string line;
                            while ((line = reader.ReadLine()) != null)
                            {
                                cancellationToken.ThrowIfCancellationRequested();

                                string trimmed = line.Trim();
                                if (string.IsNullOrEmpty(trimmed)) continue;

                                if (trimmed.StartsWith("data:", StringComparison.OrdinalIgnoreCase))
                                {
                                    isSseStream = true;
                                    string dataPart = trimmed.Substring(5).Trim();
                                    if (dataPart == "[DONE]") break;

                                    try
                                    {
                                        Dictionary<string, object> chunk = ser.Deserialize<Dictionary<string, object>>(dataPart);
                                        if (chunk != null && chunk.ContainsKey("choices"))
                                        {
                                            IList choices = chunk["choices"] as IList;
                                            if (choices != null && choices.Count > 0)
                                            {
                                                Dictionary<string, object> c0 = choices[0] as Dictionary<string, object>;
                                                if (c0 != null)
                                                {
                                                    Dictionary<string, object> delta = null;
                                                    if (c0.ContainsKey("delta")) delta = c0["delta"] as Dictionary<string, object>;
                                                    else if (c0.ContainsKey("message")) delta = c0["message"] as Dictionary<string, object>;

                                                    if (delta != null)
                                                    {
                                                        if (delta.ContainsKey("reasoning_content") && delta["reasoning_content"] != null)
                                                        {
                                                            string rc = delta["reasoning_content"].ToString();
                                                            if (!string.IsNullOrEmpty(rc))
                                                            {
                                                                sawReasoningTokens = true;
                                                            }
                                                        }

                                                        if (delta.ContainsKey("content") && delta["content"] != null)
                                                        {
                                                            string tokenText = delta["content"].ToString();
                                                            if (!string.IsNullOrEmpty(tokenText))
                                                            {
                                                                contentBuilder.Append(tokenText);
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    catch { }

                                    if (onStreamingDisplayUpdate != null && uiThrottle.ElapsedMilliseconds >= 80)
                                    {
                                        uiThrottle.Restart();
                                        string curText = contentBuilder.ToString();
                                        if (string.IsNullOrWhiteSpace(curText) && sawReasoningTokens)
                                        {
                                            onStreamingDisplayUpdate("🧠 Model is thinking deeply (reasoning chain hidden)...");
                                        }
                                        else if (!string.IsNullOrWhiteSpace(curText))
                                        {
                                            string formatted = WhitelistManager.FormatModelTextForDisplay(curText);
                                            if (!string.IsNullOrWhiteSpace(formatted))
                                            {
                                                onStreamingDisplayUpdate(formatted);
                                            }
                                        }
                                    }
                                }
                                else if (!isSseStream)
                                {
                                    rawFallbackBuilder.AppendLine(line);
                                }
                            }

                            if (isSseStream)
                            {
                                string finalContent = contentBuilder.ToString();
                                if (onStreamingDisplayUpdate != null)
                                {
                                    string formatted = WhitelistManager.FormatModelTextForDisplay(finalContent);
                                    if (!string.IsNullOrWhiteSpace(formatted))
                                    {
                                        onStreamingDisplayUpdate(formatted);
                                    }
                                }
                                return finalContent;
                            }

                            // Non-streaming JSON response fallback
                            string fullJson = rawFallbackBuilder.ToString();
                            Dictionary<string, object> root = ser.Deserialize<Dictionary<string, object>>(fullJson);
                            if (root != null && root.ContainsKey("choices"))
                            {
                                IList choices = root["choices"] as IList;
                                if (choices != null && choices.Count > 0)
                                {
                                    Dictionary<string, object> choice0 = choices[0] as Dictionary<string, object>;
                                    if (choice0 != null && choice0.ContainsKey("message"))
                                    {
                                        Dictionary<string, object> msgObj = choice0["message"] as Dictionary<string, object>;
                                        if (msgObj != null)
                                        {
                                            string content = msgObj.ContainsKey("content") && msgObj["content"] != null
                                                ? msgObj["content"].ToString()
                                                : "";
                                            if (onStreamingDisplayUpdate != null)
                                            {
                                                onStreamingDisplayUpdate(WhitelistManager.FormatModelTextForDisplay(content));
                                            }
                                            return content;
                                        }
                                    }
                                }
                            }

                            throw new Exception("OpenAI-compatible response parsing failed: " + fullJson);
                        }
                    }
                    catch (WebException wex)
                    {
                        if (cancellationToken.IsCancellationRequested)
                        {
                            throw new OperationCanceledException("Request was cancelled.");
                        }
                        if (wex.Response != null)
                        {
                            try
                            {
                                using (Stream errStream = wex.Response.GetResponseStream())
                                using (StreamReader errReader = new StreamReader(errStream, Encoding.UTF8))
                                {
                                    string errBody = errReader.ReadToEnd();
                                    HttpWebResponse httpResp = wex.Response as HttpWebResponse;
                                    int status = httpResp != null ? (int)httpResp.StatusCode : 0;
                                    throw new Exception(string.Format("HTTP {0} error: {1}", status, errBody));
                                }
                            }
                            catch (Exception innerEx)
                            {
                                throw new Exception(innerEx.Message);
                            }
                        }
                        throw new Exception("Network request failed: " + wex.Message);
                    }
                }
            });
        }

        private static async Task<string> HttpPostJsonAsync(
            string url,
            string jsonBody,
            Dictionary<string, string> headers,
            CancellationToken cancellationToken)
        {
            return await Task.Run(() =>
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "POST";
                req.ContentType = "application/json; charset=utf-8";
                req.Accept = "application/json";
                req.Timeout = 120000;
                req.ReadWriteTimeout = 120000;

                if (headers != null)
                {
                    foreach (KeyValuePair<string, string> kv in headers)
                    {
                        req.Headers[kv.Key] = kv.Value;
                    }
                }

                byte[] bodyBytes = Encoding.UTF8.GetBytes(jsonBody);
                req.ContentLength = bodyBytes.Length;

                using (CancellationTokenRegistration reg = cancellationToken.Register(() =>
                {
                    try { req.Abort(); } catch { }
                }))
                {
                    try
                    {
                        using (Stream reqStream = req.GetRequestStream())
                        {
                            reqStream.Write(bodyBytes, 0, bodyBytes.Length);
                        }

                        using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                        using (Stream respStream = resp.GetResponseStream())
                        using (StreamReader reader = new StreamReader(respStream, Encoding.UTF8))
                        {
                            return reader.ReadToEnd();
                        }
                    }
                    catch (WebException wex)
                    {
                        if (cancellationToken.IsCancellationRequested)
                        {
                            throw new OperationCanceledException("Request was cancelled.");
                        }
                        if (wex.Response != null)
                        {
                            try
                            {
                                using (Stream errStream = wex.Response.GetResponseStream())
                                using (StreamReader errReader = new StreamReader(errStream, Encoding.UTF8))
                                {
                                    string errBody = errReader.ReadToEnd();
                                    HttpWebResponse httpResp = wex.Response as HttpWebResponse;
                                    int status = httpResp != null ? (int)httpResp.StatusCode : 0;
                                    throw new Exception(string.Format("HTTP {0} error: {1}", status, errBody));
                                }
                            }
                            catch (Exception innerEx)
                            {
                                throw new Exception(innerEx.Message);
                            }
                        }
                        throw new Exception("Network request failed: " + wex.Message);
                    }
                }
            });
        }

        private static async Task<string> HttpGetAsync(string url, int timeoutMs)
        {
            return await Task.Run(() =>
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "GET";
                req.Timeout = timeoutMs;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                using (Stream s = resp.GetResponseStream())
                using (StreamReader r = new StreamReader(s, Encoding.UTF8))
                {
                    return r.ReadToEnd();
                }
            });
        }

        private static async Task<string> RunLocalSandboxEngineAsync(
            string filesSnapshotBlock,
            IList<ChatHistoryItem> history,
            IList<WhitelistedFile> whitelist,
            CancellationToken cancellationToken,
            Action<string> onStreamingDisplayUpdate)
        {
            await Task.Delay(250, cancellationToken);

            string lastUserMsg = "";
            if (history != null && history.Count > 0)
            {
                for (int i = history.Count - 1; i >= 0; i--)
                {
                    if (string.Equals(history[i].Role, "user", StringComparison.OrdinalIgnoreCase))
                    {
                        lastUserMsg = history[i].Content ?? "";
                        break;
                    }
                }
            }

            if (lastUserMsg.StartsWith("[Terminal Execution Feedback") || lastUserMsg.StartsWith("【系统终端执行结果反馈"))
            {
                if (lastUserMsg.Contains("Exit Code: 0"))
                {
                    string summary = "✅ Terminal execution completed (Exit Code 0). Output matches expectations, all tests verified successfully!";
                    if (onStreamingDisplayUpdate != null) onStreamingDisplayUpdate(summary);
                    return summary;
                }
            }

            if (whitelist == null || whitelist.Count == 0)
            {
                string verifyCmdEmpty = VerificationRunner.ExtractConversationalVerifyCommand(lastUserMsg, whitelist);
                if (!string.IsNullOrWhiteSpace(verifyCmdEmpty))
                {
                    string cmdReply = "Executing terminal command `" + verifyCmdEmpty + "`:\r\n<metis_cmd>\r\n" + verifyCmdEmpty + "\r\n</metis_cmd>";
                    if (onStreamingDisplayUpdate != null) onStreamingDisplayUpdate(WhitelistManager.FormatModelTextForDisplay(cmdReply));
                    return cmdReply;
                }
                return "No files are currently attached to this workspace. You can drag and drop files or folders, or specify what you would like to run.";
            }

            WhitelistedFile target = whitelist[0];
            string code = target.ReadCurrentContent();
            string verifyCmd = VerificationRunner.ExtractConversationalVerifyCommand(lastUserMsg, whitelist);

            StringBuilder resp = new StringBuilder();

            if (code.Contains("is_token_expired") || code.Contains("verify_token") || code.Contains("expires_at"))
            {
                string fixedCode = code;
                bool changed = false;

                if (Regex.IsMatch(fixedCode, @"def\s+is_token_expired[\s\S]*?return\s+current_time\s*<\s*expires_at"))
                {
                    fixedCode = Regex.Replace(
                        fixedCode,
                        @"(def\s+is_token_expired[\s\S]*?return\s+)current_time\s*<\s*expires_at",
                        "${1}current_time >= expires_at");
                    changed = true;
                }
                else if (Regex.IsMatch(fixedCode, @"def\s+is_token_expired[\s\S]*?return\s+current_time\s*>\s*expires_at"))
                {
                    fixedCode = Regex.Replace(
                        fixedCode,
                        @"(def\s+is_token_expired[\s\S]*?return\s+)current_time\s*>\s*expires_at",
                        "${1}current_time >= expires_at");
                    changed = true;
                }

                if (changed)
                {
                    resp.AppendLine(string.Format("Inspected whitelisted file `{0}`:", target.FileName));
                    resp.AppendLine("- **Issue Identified**: The comparison operator in `is_token_expired` is inverted or missing the boundary condition (when `current_time >= expires_at`, the token has expired and should return `True`).");
                    resp.AppendLine(string.Format("- **Fix Plan**: Apply a surgical SEARCH/REPLACE block to fix the expiration check in `{0}`, then trigger the verification command to confirm tests pass.", target.FileName));
                    resp.AppendLine();
                    resp.AppendLine(string.Format("<metis_replace file=\"{0}\">", target.FileName));
                    resp.AppendLine("<<<<<<< SEARCH");
                    if (code.Contains("expires_at = token.get(\"expires_at\", 0)"))
                    {
                        resp.AppendLine("    expires_at = token.get(\"expires_at\", 0)");
                        resp.AppendLine("    return current_time < expires_at");
                        resp.AppendLine("=======");
                        resp.AppendLine("    expires_at = token.get(\"expires_at\", 0)");
                        resp.AppendLine("    return current_time >= expires_at");
                    }
                    else if (code.Contains("current_time < expires_at"))
                    {
                        resp.AppendLine("    return current_time < expires_at");
                        resp.AppendLine("=======");
                        resp.AppendLine("    return current_time >= expires_at");
                    }
                    else
                    {
                        resp.AppendLine("    return current_time > expires_at");
                        resp.AppendLine("=======");
                        resp.AppendLine("    return current_time >= expires_at");
                    }
                    resp.AppendLine(">>>>>>> REPLACE");
                    resp.AppendLine("</metis_replace>");
                    if (!string.IsNullOrWhiteSpace(verifyCmd))
                    {
                        resp.AppendLine("<metis_cmd>");
                        resp.AppendLine(verifyCmd);
                        resp.AppendLine("</metis_cmd>");
                    }
                    string finalResp = resp.ToString();
                    if (onStreamingDisplayUpdate != null)
                    {
                        onStreamingDisplayUpdate(WhitelistManager.FormatModelTextForDisplay(finalResp));
                    }
                    return finalResp;
                }
            }

            if (lastUserMsg.IndexOf("quicksort", StringComparison.OrdinalIgnoreCase) >= 0 || lastUserMsg.Contains("快排"))
            {
                string qsCode =
@"def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)


if __name__ == ""__main__"":
    sample = [3, 6, 8, 10, 1, 2, 1]
    print(quicksort(sample))
";
                resp.AppendLine(string.Format("Generated quicksort implementation for `{0}`:", target.FileName));
                resp.AppendLine(string.Format("<metis_replace file=\"{0}\">", target.FileName));
                resp.AppendLine("<<<<<<< SEARCH");
                resp.AppendLine(code.Trim());
                resp.AppendLine("=======");
                resp.AppendLine(qsCode.TrimEnd());
                resp.AppendLine(">>>>>>> REPLACE");
                resp.AppendLine("</metis_replace>");
                if (!string.IsNullOrWhiteSpace(verifyCmd))
                {
                    resp.AppendLine("<metis_cmd>");
                    resp.AppendLine(verifyCmd);
                    resp.AppendLine("</metis_cmd>");
                }
                string finalResp = resp.ToString();
                if (onStreamingDisplayUpdate != null)
                {
                    onStreamingDisplayUpdate(WhitelistManager.FormatModelTextForDisplay(finalResp));
                }
                return finalResp;
            }

            resp.AppendLine(string.Format("[Offline Mock Sandbox Engine] Read whitelisted file `{0}` ({1} bytes).", target.FileName, code.Length));
            if (!string.IsNullOrWhiteSpace(verifyCmd))
            {
                resp.AppendLine(string.Format("Detected verification command `{0}`, executing:", verifyCmd));
                resp.AppendLine("<metis_cmd>");
                resp.AppendLine(verifyCmd);
                resp.AppendLine("</metis_cmd>");
            }
            return resp.ToString();
        }
    }
}
