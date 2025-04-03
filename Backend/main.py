import io
import os
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from google import genai
from newspaper import Article
import numpy as np
import requests
import webvtt
import yt_dlp

app = Flask(__name__)
CORS(app)

load_dotenv()

geminiClient = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def scrape_article(url: str) -> dict:
    url = url.strip().replace("\n", "").replace("'", "").replace('"', "")
    print(f"Scraping Article: {url}")
    article = Article(url)
    article.download()
    article.parse()
    article.nlp()

    result = {
        "title": article.title,
        "text": article.text,
        "summary": article.summary,
        "keywords": article.keywords,
    }

    return result


def scrape_yt(url: str) -> str:
    url = url.strip().replace("'", "").replace('"', "").replace("\n", "")
    print(f"Scraping YouTube: {url}")

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "writeinfojson": False,
    }

    if "youtube.com/shorts" in url:
        return "Cannot scrape YouTube Shorts, try some other long form video"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        output = {
            "Description": "",
            "Sponsor Transcript": "",
            "Sponsorship Retention Ratio": 1,
            "Comments": [],
        }
        info = ydl.extract_info(url, download=False)
        output["Description"] = info.get("description", "No description available.")

        try:
            # Get sponsor segments
            sponsor_segments = []
            response = requests.get(
                f"https://sponsor.ajay.app/api/skipSegments?videoID={info['id']}&category=sponsor"
            )
            if response.status_code == 200:
                sponsor_segments = response.json()
        except:
            sponsor_segments = []

        try:
            # Get retention data
            retention_data = info.get("heatmap")

            # Calculate Sponsorship Retention Ratio. It is the median of the sponsor retention values divided by the median of the non-sponsor retention values.
            if retention_data:
                sponsor_retention = []
                non_sponsor_retention = []
                for segment in sponsor_segments:
                    start, end = segment["segment"]
                    for retention in retention_data:
                        if (
                            start <= retention["start_time"]
                            and end >= retention["end_time"]
                        ):
                            sponsor_retention.append(retention["value"])
                        else:
                            non_sponsor_retention.append(retention["value"])
                output["Sponsorship Retention Ratio"] = round(
                    (np.median(sponsor_retention) / np.median(non_sponsor_retention)), 4
                )
            else:
                output["Sponsorship Retention Ratio"] = -1
        except:
            output["Sponsorship Retention Ratio"] = -1

        try:
            # Get English subtitles
            en_subtitle = info.get("requested_subtitles", {}).get("en") or info.get(
                "requested_subtitles", {}
            ).get("en-auto")

            # Process sponsor transcript from subtitles
            if en_subtitle and sponsor_segments:
                subtitle_url = en_subtitle["url"]
                subtitle_content = ydl.urlopen(subtitle_url).read().decode("utf-8")
                vtt = webvtt.read_buffer(io.StringIO(subtitle_content))

                seen_lines = set()
                for segment in sponsor_segments:
                    start, end = segment["segment"]
                    for caption in vtt:
                        if (
                            start <= caption.start_in_seconds <= end
                            or start <= caption.end_in_seconds <= end
                        ):
                            for line in caption.text.strip().splitlines():
                                if line not in seen_lines:
                                    output["Sponsor Transcript"] += line + " "
                                    seen_lines.add(line)
            else:
                output["Sponsor Transcript"] = (
                    "No sponsor segments or English subtitles available."
                )
        except:
            output["Sponsor Transcript"] = "No sponsor segments or English subtitles available."

        try:
            # Get comments
            video_id = url.split("=")[-1]
            params = {
                "part": "snippet",
                "videoId": video_id,
                "maxResults": "50",
                "textFormat": "plainText",
                "key": os.environ.get("COMMENTS_API_KEY"),
            }

            response = requests.get(
                "https://www.googleapis.com/youtube/v3/commentThreads",
                params=params,
                headers={"Referer": "https://ytcomment.kmcat.uk/"},
            )

            # Extract comments
            output["Comments"] = [
                comment["snippet"]["topLevelComment"]["snippet"]["textDisplay"].strip()
                for comment in response.json().get("items", [])
            ]
        except:
            output["Comments"] = []

        return output


def scrape_reddit(subreddit: str) -> dict:
    subreddit = subreddit.strip().replace("\n", "").replace("'", "").replace('"', "")
    print(f"Scraping Subreddit: {subreddit}")
    url = f"https://www.reddit.com/r/{subreddit}/top/.json?t=all"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
    }

    output = {}
    response = requests.get(url=url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        for i in range(10):
            try:
                output[i] = {
                    "title": data["data"]["children"][i]["data"]["title"],
                    "description": data["data"]["children"][i]["data"]["selftext"],
                    "url": "https://www.reddit.com"
                    + data["data"]["children"][i]["data"]["permalink"],
                    "upvote_ratio": data["data"]["children"][i]["data"]["upvote_ratio"],
                    "comments": {},
                }
            except:
                break
    else:
        print("Failed to fetch data from Reddit")
        print(response.status_code)
        return "Could not scrape reddit"

    for i in output:
        url = output[i]["url"] + ".json?sort=top"
        r = requests.get(url=url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            try:
                for j in range(10):
                    output[i]["comments"][j] = {
                        "text": data[1]["data"]["children"][j]["data"]["body"],
                        "upvotes": data[1]["data"]["children"][j]["data"]["ups"],
                        "replies": {},
                    }
                    try:
                        for k in range(5):
                            output[i]["comments"][j]["replies"][k] = {
                                "text": data[1]["data"]["children"][j]["data"][
                                    "replies"
                                ]["data"]["children"][k]["data"]["body"],
                                "upvotes": data[1]["data"]["children"][j]["data"][
                                    "replies"
                                ]["data"]["children"][k]["data"]["ups"],
                            }
                    except:
                        pass
            except Exception as e:
                pass
        else:
            print("Failed to fetch comments from Reddit")
            print(r.status_code)

    return output


def search_youtube(search_query: str) -> dict:
    search_query = (
        search_query.strip().replace("\n", "").replace("'", "").replace('"', "")
    )
    print(f"Searching YouTube: {search_query}")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "geo_bypass": True,
        "noplaylist": True,
        "postprocessor_args": ["-match_lang", "en"],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Search for the query on YouTube
            result = ydl.extract_info(f"ytsearch50:{search_query}", download=False)
            videos = result.get("entries", [])

            # Sort the videos based on view count and get the top 5
            sorted_videos = sorted(
                videos, key=lambda x: x.get("view_count", 0), reverse=True
            )

            # Create a dictionary for the top 5 videos
            top_videos = {
                i: {
                    "title": video.get("title", "Unknown"),
                    "url": video.get("url", ""),
                }
                for i, video in enumerate(sorted_videos[:5])
            }

            return top_videos
    except Exception as e:
        return {"error": str(e)}

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "Welcome to the DeepCoin API"})

@app.route('/yt/search', methods=['POST'])
def yt_search():
    data = request.get_json()
    search_query = data.get("search_query")
    if not search_query:
        return jsonify({"error": "No search query provided"}), 400

    result = search_youtube(search_query)
    return jsonify(result)

@app.route('/yt/scrape', methods=['POST'])
def yt_scrape():
    data = request.get_json()
    url = data.get("url")
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    result = scrape_yt(url)
    return jsonify(result)

@app.route('/reddit/scrape', methods=['POST'])
def reddit_scrape():
    data = request.get_json()
    subreddit = data.get("subreddit")
    if not subreddit:
        return jsonify({"error": "No subreddit provided"}), 400

    result = scrape_reddit(subreddit)
    return jsonify(result)

@app.route('/article/scrape', methods=['POST'])
def article_scrape():
    data = request.get_json()
    url = data.get("url")
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    result = scrape_article(url)
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=os.getenv("DEBUG", False), host="0.0.0.0", port=8000)
