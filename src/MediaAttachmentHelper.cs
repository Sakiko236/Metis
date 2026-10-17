using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Text;

namespace Metis
{
    public static class MediaAttachmentHelper
    {
        private static readonly HashSet<string> ImageExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"
        };

        public static bool IsImageFile(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return false;
            try
            {
                string ext = Path.GetExtension(path);
                return !string.IsNullOrEmpty(ext) && ImageExtensions.Contains(ext);
            }
            catch
            {
                return false;
            }
        }

        public static bool IsPdfFile(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return false;
            try
            {
                string ext = Path.GetExtension(path);
                return string.Equals(ext, ".pdf", StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return false;
            }
        }

        public static bool IsMediaAttachment(string path)
        {
            return IsImageFile(path) || IsPdfFile(path);
        }

        public static string GetMimeType(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return "application/octet-stream";
            string ext = "";
            try { ext = Path.GetExtension(path).ToLowerInvariant(); } catch { }
            switch (ext)
            {
                case ".png": return "image/png";
                case ".jpg":
                case ".jpeg": return "image/jpeg";
                case ".webp": return "image/webp";
                case ".gif": return "image/gif";
                case ".bmp": return "image/bmp";
                case ".pdf": return "application/pdf";
                default: return "application/octet-stream";
            }
        }

        /// <summary>
        /// Reads an image or PDF file as Base64 for multimodal API upload.
        /// For large images (>2048px or >3MB) or BMP files, automatically normalizes/resizes to PNG/JPEG so all APIs accept it.
        /// </summary>
        public static string GetBase64ForApi(string fullPath, out string effectiveMimeType)
        {
            effectiveMimeType = GetMimeType(fullPath);
            if (string.IsNullOrWhiteSpace(fullPath) || !File.Exists(fullPath))
            {
                return "";
            }

            try
            {
                if (IsImageFile(fullPath))
                {
                    FileInfo fi = new FileInfo(fullPath);
                    string ext = Path.GetExtension(fullPath).ToLowerInvariant();

                    // Convert .bmp or large images (> 2MB) using System.Drawing so Vision APIs handle them cleanly
                    if (ext == ".bmp" || fi.Length > 2 * 1024 * 1024)
                    {
                        try
                        {
                            using (Image img = Image.FromFile(fullPath))
                            {
                                int maxDim = 2048;
                                int w = img.Width;
                                int h = img.Height;
                                if (w > maxDim || h > maxDim)
                                {
                                    double ratio = Math.Min((double)maxDim / w, (double)maxDim / h);
                                    w = Math.Max(1, (int)(w * ratio));
                                    h = Math.Max(1, (int)(h * ratio));
                                }

                                using (Bitmap bmp = new Bitmap(w, h))
                                {
                                    using (Graphics g = Graphics.FromImage(bmp))
                                    {
                                        g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                                        g.DrawImage(img, 0, 0, w, h);
                                    }
                                    using (MemoryStream ms = new MemoryStream())
                                    {
                                        if (ext == ".jpg" || ext == ".jpeg")
                                        {
                                            bmp.Save(ms, ImageFormat.Jpeg);
                                            effectiveMimeType = "image/jpeg";
                                        }
                                        else
                                        {
                                            bmp.Save(ms, ImageFormat.Png);
                                            effectiveMimeType = "image/png";
                                        }
                                        return Convert.ToBase64String(ms.ToArray());
                                    }
                                }
                            }
                        }
                        catch { }
                    }
                }

                byte[] rawBytes = File.ReadAllBytes(fullPath);
                return Convert.ToBase64String(rawBytes);
            }
            catch
            {
                return "";
            }
        }

        public static string BuildImageMetadataSummary(string fullPath)
        {
            try
            {
                FileInfo fi = new FileInfo(fullPath);
                int w = 0;
                int h = 0;
                try
                {
                    using (Image img = Image.FromFile(fullPath))
                    {
                        w = img.Width;
                        h = img.Height;
                    }
                }
                catch { }

                string dimStr = (w > 0 && h > 0) ? string.Format("{0}x{1} px, ", w, h) : "";
                return string.Format(
                    "[Image File: {0} ({1}{2} bytes, MIME: {3}) — ready for native multimodal transmission]",
                    fi.Name, dimStr, fi.Length, GetMimeType(fullPath));
            }
            catch
            {
                return "[Image File: " + Path.GetFileName(fullPath) + "]";
            }
        }

        /// <summary>
        /// Summarizes PDF file metadata for display and context attachment.
        /// Metis does not extract text locally; PDFs are transferred natively as Base64 multimodal media to LLMs.
        /// </summary>
        public static string BuildPdfMetadataSummary(string pdfPath)
        {
            if (string.IsNullOrWhiteSpace(pdfPath) || !File.Exists(pdfPath))
            {
                return "";
            }

            try
            {
                FileInfo fi = new FileInfo(pdfPath);
                return string.Format(
                    "[PDF Document: {0} ({1} bytes, MIME: application/pdf) — ready for native multimodal upload]",
                    fi.Name, fi.Length);
            }
            catch
            {
                return "[PDF Document: " + Path.GetFileName(pdfPath) + "]";
            }
        }

        /// <summary>
        /// Saves a pasted clipboard image (data URL or raw base64) to %APPDATA%\Metis\attachments and returns its file path.
        /// </summary>
        public static string SavePastedImageToDisk(string dataUrlOrBase64, string fileNameHint = null)
        {
            if (string.IsNullOrWhiteSpace(dataUrlOrBase64)) return null;

            string ext = ".png";
            string base64 = dataUrlOrBase64.Trim();

            if (base64.StartsWith("data:", StringComparison.OrdinalIgnoreCase))
            {
                int commaIdx = base64.IndexOf(',');
                if (commaIdx > 0)
                {
                    string header = base64.Substring(0, commaIdx).ToLowerInvariant();
                    if (header.Contains("image/jpeg") || header.Contains("image/jpg")) ext = ".jpg";
                    else if (header.Contains("image/webp")) ext = ".webp";
                    else if (header.Contains("image/gif")) ext = ".gif";
                    else if (header.Contains("application/pdf")) ext = ".pdf";
                    base64 = base64.Substring(commaIdx + 1);
                }
            }

            byte[] bytes = Convert.FromBase64String(base64);
            string attachDir = Path.Combine(ConfigManager.GetAppDataDir(), "attachments");
            if (!Directory.Exists(attachDir))
            {
                Directory.CreateDirectory(attachDir);
            }

            string safeName = string.IsNullOrWhiteSpace(fileNameHint)
                ? ("paste_" + DateTime.Now.ToString("yyyyMMdd_HHmmss_fff") + ext)
                : Path.GetFileName(fileNameHint);
            if (string.IsNullOrEmpty(Path.GetExtension(safeName)))
            {
                safeName += ext;
            }

            string fullPath = Path.Combine(attachDir, safeName);
            File.WriteAllBytes(fullPath, bytes);
            return fullPath;
        }
    }
}
