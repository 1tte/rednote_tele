import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": ("en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"),
    "Referer": "https://www.xiaohongshu.com/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)


# ============================================================
# URL NORMALIZER
# ============================================================


def normalize_page_url(url: str) -> str:
    """
    Support:
    - https://www.rednote.com/discovery/item/...
    - https://www.xiaohongshu.com/discovery/item/...
    - view-source:https://...
    """

    url = url.strip()

    if url.startswith("view-source:"):
        url = url[len("view-source:") :]

    url = url.replace(
        "https://www.rednote.com/",
        "https://www.xiaohongshu.com/",
    )

    url = url.replace(
        "http://www.rednote.com/",
        "https://www.xiaohongshu.com/",
    )

    return url


# ============================================================
# FETCH PAGE
# ============================================================


def get_html(url: str) -> str:
    url = normalize_page_url(url)

    print()
    print("=" * 90)
    print("FETCH PAGE")
    print("=" * 90)

    print(url)

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )

    print()
    print("[STATUS]")
    print(response.status_code)

    print()
    print("[FINAL URL]")
    print(response.url)

    print()
    print("[HTML SIZE]")
    print(f"{len(response.text):,} chars")

    response.raise_for_status()

    return response.text


# ============================================================
# CLEAN HTML / ESCAPED DATA
# ============================================================


def normalize_source_text(text: str) -> str:
    """
    Xiaohongshu source can contain escaped URLs.
    """

    return (
        text.replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("\\u003D", "=")
        .replace("\\u003d", "=")
    )


# ============================================================
# JSON-LD PARSER
# ============================================================


def find_videoobject_recursive(obj):
    """
    Recursively find:
        "@type": "VideoObject"
    """

    if isinstance(obj, dict):

        obj_type = obj.get("@type")

        if obj_type == "VideoObject":
            return obj

        if isinstance(obj_type, list) and "VideoObject" in obj_type:
            return obj

        for value in obj.values():
            result = find_videoobject_recursive(value)

            if result:
                return result

    elif isinstance(obj, list):

        for item in obj:
            result = find_videoobject_recursive(item)

            if result:
                return result

    return None


def get_ld_json_video(html: str) -> dict:
    """
    First try BeautifulSoup JSON-LD parsing.
    Then use raw regex fallback.
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    scripts = soup.find_all(
        "script",
        attrs={"type": "application/ld+json"},
    )

    print()
    print(f"[LD+JSON SCRIPT FOUND] {len(scripts)}")

    # ========================================================
    # METHOD 1
    # BeautifulSoup
    # ========================================================

    for index, script in enumerate(
        scripts,
        start=1,
    ):

        raw = (script.string or script.get_text() or "").strip()

        if not raw:
            continue

        print(f"[CHECK LD+JSON #{index}] " f"{raw[:90]!r}")

        try:
            data = json.loads(raw)

        except json.JSONDecodeError:
            continue

        video = find_videoobject_recursive(data)

        if video:
            print("[+] VideoObject ditemukan " "dari JSON-LD.")

            return video

    # ========================================================
    # METHOD 2
    # Raw HTML regex
    # ========================================================

    print()
    print("[FALLBACK] Scan raw HTML untuk JSON-LD...")

    pattern = re.compile(
        r"<script\b[^>]*"
        r'type=["\']application/ld\+json["\']'
        r"[^>]*>"
        r"(.*?)"
        r"</script>",
        re.IGNORECASE | re.DOTALL,
    )

    for raw in pattern.findall(html):

        raw = raw.strip()

        if "VideoObject" not in raw and "contentUrl" not in raw:
            continue

        try:
            data = json.loads(raw)

        except json.JSONDecodeError:
            continue

        video = find_videoobject_recursive(data)

        if video:
            print("[+] VideoObject ditemukan " "dari raw HTML.")

            return video

    # ========================================================
    # METHOD 3
    # Last fallback: search contentUrl around VideoObject
    # ========================================================

    normalized = normalize_source_text(html)

    if "VideoObject" in normalized:

        content_match = re.search(
            r'"contentUrl"\s*:\s*"([^"]+)"',
            normalized,
            re.IGNORECASE,
        )

        if content_match:

            print("[+] contentUrl ditemukan via " "raw fallback.")

            return {
                "@type": "VideoObject",
                "contentUrl": content_match.group(1),
            }

    raise RuntimeError("VideoObject tidak ditemukan di HTML Xiaohongshu.")


# ============================================================
# VIDEO METADATA
# ============================================================


def print_video_metadata(video: dict):
    print()
    print("=" * 90)
    print("VIDEO METADATA")
    print("=" * 90)

    fields = [
        ("Name", "name"),
        ("Description", "description"),
        ("Thumbnail", "thumbnailUrl"),
        ("Upload Date", "uploadDate"),
        ("Duration", "duration"),
    ]

    for label, key in fields:
        print(
            f"{label:<12}:",
            video.get(key, ""),
        )

    print()
    print("CONTENT URL:")
    print(video.get("contentUrl", ""))


# ============================================================
# EXTRACT 259
# ============================================================


def extract_259_identifier(content_url: str) -> str:

    content_url = normalize_source_text(content_url)

    match = re.search(
        r"/(?:stream/)?1/110/259/" r"([^/?]+?)_259\.mp4",
        content_url,
        re.IGNORECASE,
    )

    if not match:

        match = re.search(
            r"/259/" r"([^/?]+?)_259\.mp4",
            content_url,
            re.IGNORECASE,
        )

    if not match:
        raise RuntimeError("Identifier stream 259 tidak ditemukan " "dari contentUrl.")

    return match.group(1)


# ============================================================
# FIND 258 FROM PAGE SOURCE
# ============================================================


def get_identifier_family_prefix(identifier_259: str) -> str:
    """
    Example:

    01ea190f6c13fd96010370039e71e584f3

    We don't assume the tail hash is derivable.

    Use the stable-looking beginning to rank sibling
    candidates.

    16 chars is intentionally conservative.
    """

    return identifier_259[:16]


def find_258_identifiers(
    html: str,
    identifier_259: str,
) -> list[str]:

    source = normalize_source_text(html)

    candidates = []

    # ========================================================
    # Search explicit 258 stream identifiers
    # ========================================================

    patterns = [
        # full path
        r"/stream/1/110/258/" r"([A-Za-z0-9_-]+?)_258\.mp4",
        # alternate path occurrence
        r"/1/110/258/" r"([A-Za-z0-9_-]+?)_258\.mp4",
        # filename only
        r"([A-Za-z0-9_-]{20,})_258\.mp4",
    ]

    for pattern in patterns:

        matches = re.findall(
            pattern,
            source,
            flags=re.IGNORECASE,
        )

        for identifier in matches:

            if identifier not in candidates:
                candidates.append(identifier)

    print()
    print(f"[RAW 258 IDENTIFIERS FOUND] " f"{len(candidates)}")

    # ========================================================
    # Rank same-family candidates
    # ========================================================

    prefix = get_identifier_family_prefix(identifier_259)

    print("[259 FAMILY PREFIX]", prefix)

    same_family = [
        identifier for identifier in candidates if identifier.startswith(prefix)
    ]

    if same_family:

        print(f"[SAME FAMILY FOUND] " f"{len(same_family)}")

        candidates = same_family

    return candidates


# ============================================================
# BUILD 258 URL
# ============================================================


def build_258_urls(identifier: str) -> list[str]:
    """
    Try useful CDN host variants.
    User's preferred host is sns-video-hw.
    """

    hosts = [
        "sns-video-hw.xhscdn.com",
        "sns-video-qc.xhscdn.com",
        "sns-video-bd.xhscdn.com",
    ]

    return [
        (f"https://{host}" f"/stream/1/110/258/" f"{identifier}_258.mp4")
        for host in hosts
    ]


# ============================================================
# VALIDATE VIDEO URL
# ============================================================


def validate_video_url(url: str) -> bool:

    validation_headers = {
        **HEADERS,
        "Range": "bytes=0-2047",
    }

    try:

        response = requests.get(
            url,
            headers=validation_headers,
            stream=True,
            allow_redirects=True,
            timeout=(10, 20),
        )

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        valid_status = response.status_code in (
            200,
            206,
        )

        valid_type = (
            "video" in content_type
            or "octet-stream" in content_type
            or not content_type
        )

        response.close()

        return valid_status and valid_type

    except requests.RequestException:
        return False


# ============================================================
# RESOLVE 258
# ============================================================


def resolve_real_258(
    html: str,
    content_url_259: str,
) -> str:

    if "_258.mp4" in content_url_259 or "/258/" in content_url_259:
        print()
        print("=" * 90)
        print("258 ALREADY IN CONTENT URL")
        print("=" * 90)
        parsed = urlparse(content_url_259)
        return content_url_259.replace(parsed.netloc, "sns-video-hw.xhscdn.com")

    identifier_259 = extract_259_identifier(content_url_259)

    print()
    print("=" * 90)
    print("259 IDENTIFIER")
    print("=" * 90)

    print(identifier_259)

    identifiers_258 = find_258_identifiers(
        html,
        identifier_259,
    )

    if not identifiers_258:

        raise RuntimeError(
            "Tidak menemukan identifier stream 258 "
            "di source halaman.\n\n"
            "Artinya URL 258 tidak muncul secara literal "
            "di HTML yang diterima requests."
        )

    print()
    print("=" * 90)
    print("258 CANDIDATES")
    print("=" * 90)

    tested = 0

    for identifier in identifiers_258:

        urls = build_258_urls(identifier)

        for url in urls:

            tested += 1

            print()
            print(f"[TEST #{tested}]")

            print(url)

            if validate_video_url(url):

                print()
                print("[+] VALID 258 FOUND")

                return url

            print("[-] invalid")

    raise RuntimeError(
        "Identifier 258 ditemukan tetapi tidak ada " "candidate CDN yang valid."
    )


# ============================================================
# DOWNLOAD
# ============================================================


def download_video(
    url: str,
    output_path: Path,
    progress_callback=None,
):

    print()
    print("=" * 90)
    print("DOWNLOAD")
    print("=" * 90)

    print(url)

    headers = {
        **HEADERS,
        "Referer": "https://www.xiaohongshu.com/",
    }

    response = requests.get(
        url,
        headers=headers,
        stream=True,
        timeout=(15, 120),
    )

    response.raise_for_status()

    total = int(
        response.headers.get(
            "content-length",
            0,
        )
    )

    temp_path = output_path.with_suffix(output_path.suffix + ".part")

    downloaded = 0

    with open(
        temp_path,
        "wb",
    ) as file:

        for chunk in response.iter_content(chunk_size=1024 * 1024):

            if not chunk:
                continue

            file.write(chunk)

            downloaded += len(chunk)

            if total:

                percent = downloaded / total * 100

                if progress_callback:
                    progress_callback(percent, downloaded, total)
                else:
                    print(
                        "\r"
                        f"{percent:6.2f}% "
                        f"{downloaded / 1024 / 1024:.2f}"
                        "/"
                        f"{total / 1024 / 1024:.2f} MB",
                        end="",
                    )

    temp_path.replace(output_path)

    print()
    print()
    print("[SAVED]")
    print(output_path.resolve())


# ============================================================
# NOTE ID
# ============================================================


def extract_note_id(page_url: str) -> str:

    match = re.search(
        r"/(?:discovery/item|explore)/([A-Za-z0-9]+)",
        page_url,
    )

    if match:
        return match.group(1)

    return "xiaohongshu_video"


# ============================================================
# MAIN
# ============================================================


def generate_seo_content(keyword: str) -> str:
    if not keyword or keyword == "N/A":
        return "Tidak ada keyword untuk generate SEO."
    
    api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
    api_key = "AIzaSyBUxngXkY3Y3Z95K5O4cV6sGZ_R7R3OahE"
    
    prompt = f"""Generate highly optimized YouTube Shorts SEO metadata for a video based on this original title, caption, keyword, or context:

'{keyword}'

IMPORTANT RULES:

1. LANGUAGE
- ALL generated output MUST be in natural ENGLISH, regardless of the language of the input.
- First understand and internally translate the meaning/context of the original input before generating the SEO metadata.
- Do NOT output the translation or explanation separately.
- Write like a native English YouTube creator, NOT like a literal translation.

2. UNDERSTAND THE VIDEO CONTEXT
- Identify the main subject, action, interesting moment, emotion, and likely viewer intent from the input.
- Preserve the actual meaning of the original caption.
- If the original caption contains humor, suspense, cuteness, surprise, relaxation, satisfying moments, danger, food appeal, or another strong emotion, preserve that angle naturally in English.
- Do NOT invent events, animals, locations, species, outcomes, or facts that are not supported by the input.
- If an exact species/object cannot be confidently identified, use a broader accurate term instead of guessing.
- Pay attention to any original hashtags because they may provide important context about the subject.

3. TITLE
- Generate ONE highly clickable and SEO-friendly YouTube Shorts title.
- Put the most important searchable subject or keyword naturally in the title whenever possible.
- Combine SEO relevance with curiosity and emotional appeal.
- Prefer a clear subject + interesting action/payoff rather than generic clickbait.
- Use natural capitalization. Strategic emphasis such as ONE or TWO uppercase words is allowed when it improves the hook.
- Use 1-3 relevant emojis maximum.
- End the title with #Shorts.
- Keep the title concise and easy to understand at a glance.
- Avoid misleading clickbait.
- Do not reveal the entire payoff if keeping some curiosity would improve retention.

4. DESCRIPTION
- Write a short, engaging YouTube Shorts description in natural English.
- Use short paragraphs separated by \\n\\n.
- The first 1-2 lines should immediately describe or tease the most interesting part of the video.
- Expand briefly on what is happening in the video.
- Preserve the tone of the source: funny, cute, suspenseful, relaxing, satisfying, fascinating, etc.
- End the main description with a natural engagement question when appropriate.
- Do NOT keyword-stuff the description.
- After the main description, add a blank line and place relevant hashtags at the VERY BOTTOM.
- Use approximately 6-10 highly relevant hashtags.
- Prioritize specific hashtags before broad hashtags.
- Include #Shorts.
- Do NOT put hashtags randomly inside normal sentences.

5. TAGS / SEO KEYWORDS
- Generate 30-40 highly relevant YouTube search tags.
- Tags MUST be comma-separated in ONE string.
- Start with the strongest exact-match/search-intent keywords.
- Include useful variations such as:
  * main subject
  * main subject + action
  * specific long-tail searches
  * broader niche keywords
  * relevant Shorts search terms
- Prefer tags that accurately describe THIS video over unrelated high-volume keywords.
- Do NOT generate spammy, misleading, or completely unrelated tags.
- Do NOT repeat identical tags.
- Translate useful concepts from the original-language hashtags into natural English search keywords where relevant.

6. HOOK
- Generate a short on-screen hook designed for the FIRST 0-2 SECONDS of the video.
- The hook should immediately create curiosity, suspense, emotion, humor, cuteness, surprise, or visual interest depending on the content.
- Keep it very short and easy to read on a phone.
- Prefer approximately 4-10 words.
- Use natural conversational English.
- The hook should complement the title rather than simply repeat it.
- Do not falsely describe something that does not happen in the video.
- Use 0-2 relevant emojis maximum.

7. CONTENT-SPECIFIC STRATEGY
Choose the SEO/retention angle based on the actual content:
- Animals: species/animal + behavior + cute/funny/surprising moment.
- Fishing: fish/catch + fishing type + action/adventure.
- Carnivorous plants: Venus flytrap/carnivorous plant + insect + specific interaction/outcome.
- Food: food name + texture/cooking/visual appeal + cuisine/location when known.
- Nature/marine life: specific animal/place/phenomenon + visual or emotional appeal.
- Travel: location + unique experience.
- Relaxing content: prioritize peaceful/satisfying language instead of aggressive clickbait.
- Funny content: preserve the joke or POV when it is central to the original caption.
These are guidelines, not reasons to invent missing information.

8. AVOID REPETITIVE OUTPUT
- Do not automatically use the same title formula for every video.
- Adapt wording and emotional angle to the specific input.
- Avoid repeatedly using phrases such as "You Won't Believe", "This Is Insane", or "Wait for It" unless genuinely appropriate.
- Make each video's metadata feel specific to that video's story.

9. OUTPUT FORMAT
- Output MUST be valid JSON ONLY.
- Do NOT use markdown.
- Do NOT use ```json code fences.
- Do NOT include explanations, introductions, notes, or text outside the JSON object.
- Use valid JSON escaping.
- The "desc", "tags", and "hooks" values MUST be strings.
- Represent description line breaks using \\n.
- Ensure the final response can be parsed directly using json.loads().

Return EXACTLY this JSON structure:

{{
  "title": "[SEO-friendly and engaging English title ending with #Shorts]",
  "desc": "[Short structured English description with \\n line breaks and hashtags at the very bottom]",
  "tags": "[tag 1, tag 2, tag 3, ... at least 30 highly relevant tags]",
  "hooks": "[short 0-2 second on-screen hook]"
}}
"""

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    try:
        resp = requests.post(f"{api_url}?key={api_key}", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return f"Gagal membuat SEO: {e}"


# ============================================================
# TELEGRAM BOT ENTRY (AIOGRAM)
# ============================================================

import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import FSInputFile
import os

TOKEN = '8842212596:AAF9bJKqTBs1sJMC23OzXfolK7S7fDHyKVQ'
CHAT_ID = 7271666868

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Wrapper to run blocking functions in asyncio
async def async_get_html(url: str):
    return await asyncio.to_thread(get_html, url)

async def async_get_ld_json_video(html: str):
    return await asyncio.to_thread(get_ld_json_video, html)

async def async_resolve_real_258(html: str, content_url: str):
    return await asyncio.to_thread(resolve_real_258, html, content_url)

async def async_generate_seo_content(keyword: str):
    return await asyncio.to_thread(generate_seo_content, keyword)

async def download_video_async(final_url: str, temp_filepath: str):
    def _download():
        vid_resp = requests.get(final_url, headers={**HEADERS, "Referer": "https://www.xiaohongshu.com/"}, stream=True, timeout=60)
        vid_resp.raise_for_status()
        with open(temp_filepath, 'wb') as f:
            for chunk in vid_resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    await asyncio.to_thread(_download)

@dp.message(F.text)
async def handle_message(message: types.Message):
    if message.chat.id != CHAT_ID:
        # Ignore unauthorized chats
        return

    url = message.text.strip()
    if not url.startswith('http'):
        await message.reply("Silakan kirim URL RedNote / Xiaohongshu yang valid.")
        return

    try:
        msg = await message.reply("Sedang generate SEO & mendownload video (paralel)...")
        
        page_url = normalize_page_url(url)
        html = await async_get_html(page_url)
        video = await async_get_ld_json_video(html)
        
        content_url = video.get("contentUrl")
        if not content_url:
            raise RuntimeError("VideoObject ditemukan tetapi contentUrl kosong.")
            
        content_url = normalize_source_text(content_url)
        final_url = await async_resolve_real_258(html, content_url)
        
        name = video.get("name", "N/A")
        upload_date = video.get("uploadDate", "N/A")
        
        def sanitize_filename(filename: str) -> str:
            invalid_chars = '<>:"/\\|?*\n\r\t'
            for char in invalid_chars:
                filename = filename.replace(char, ' ')
            filename = filename.strip()
            return filename if filename else "rednote_video"
            
        safe_title = sanitize_filename(name)
        temp_filepath = f"{safe_title}.mp4"
        
        # Run SEO and Video Download concurrently using asyncio.gather
        seo_task = asyncio.create_task(async_generate_seo_content(name))
        vid_task = asyncio.create_task(download_video_async(final_url, temp_filepath))
        
        seo_content, _ = await asyncio.gather(seo_task, vid_task)
                
        try:
            import json as json_lib
            # Clean up the output in case the AI wraps it in markdown block
            clean_seo = seo_content.strip()
            if clean_seo.startswith("```json"):
                clean_seo = clean_seo[7:]
            if clean_seo.startswith("```"):
                clean_seo = clean_seo[3:]
            if clean_seo.endswith("```"):
                clean_seo = clean_seo[:-3]
                
            seo_data = json_lib.loads(clean_seo.strip())
            
            seo_formatted_main = (
                f"**Title:**\n`{seo_data.get('title', '')}`\n\n"
                f"**Desc:**\n`{seo_data.get('desc', '')}`\n\n"
                f"**Hooks:**\n`{seo_data.get('hooks', '')}`\n"
            )
            seo_formatted_tags = f"**Tags:**\n`{seo_data.get('tags', '')}`\n"
        except Exception:
            seo_formatted_main = f"**SEO Data (Raw):**\n`{seo_content}`\n"
            seo_formatted_tags = ""
        
        doc_caption = (
            f"✅ **Berhasil diekstrak**\n\n"
            f"**Nama:**\n`{name}`\n\n"
            f"**Tanggal:**\n`{upload_date}`\n\n"
            f"**Link Download:**\n`{final_url}`\n"
        )
                
        await bot.edit_message_text(text="Video & SEO siap! Sedang mengirim ke Telegram...", chat_id=message.chat.id, message_id=msg.message_id)
        
        # Send document
        doc = FSInputFile(temp_filepath)
        await bot.send_document(
            chat_id=message.chat.id, 
            document=doc, 
            caption=doc_caption, 
            parse_mode="Markdown"
        )
            
        # Send SEO messages separately
        if seo_formatted_main:
            await bot.send_message(chat_id=message.chat.id, text=seo_formatted_main, parse_mode="Markdown")
        if seo_formatted_tags:
            await bot.send_message(chat_id=message.chat.id, text=seo_formatted_tags, parse_mode="Markdown")
            
        # Clean up
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
            
        await bot.delete_message(chat_id=message.chat.id, message_id=msg.message_id)
        
    except Exception as e:
        await message.reply(f"❌ Terjadi kesalahan:\n{str(e)}")

async def main_async():
    print("Tele bot (aiogram) is running...")
    # Drop pending updates and start polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main_async())
