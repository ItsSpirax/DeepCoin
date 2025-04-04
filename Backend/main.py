import io
import json
import os

import numpy as np
import requests
import torch
import webvtt
import yt_dlp
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from google import genai
from newspaper import Article, Config
from transformers import AutoModelForSequenceClassification, AutoTokenizer


app = Flask(__name__)
CORS(app)

model_name = "ProsusAI/finbert"

load_dotenv()

geminiClient = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
tokenizer = AutoTokenizer.from_pretrained("model")
model = AutoModelForSequenceClassification.from_pretrained("model")

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)
model.eval()

label_map = {0: "Positive", 1: "Negative"}


def predict_sentiment(text):
    inputs = tokenizer(text, padding=True, truncation=True, return_tensors="pt").to(
        device
    )

    with torch.no_grad():
        outputs = model(**inputs)
        predictions = torch.nn.functional.softmax(outputs.logits, dim=-1)

    predicted_class = torch.argmax(predictions).item()

    return predicted_class


def scrape_coinmarketcap(coin: str) -> dict:
    coin = coin.strip().replace("\n", "").replace("'", "").replace('"', "")
    print(f"Scraping CoinMarketCap: {coin}")
    url = f"https://pro-api.coinmarketcap.com/v1/cryptocurrency/ohlcv/latest?symbol={coin}&CMC_PRO_API_KEY={os.getenv('CMC_API_KEY')}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data["data"][coin]
    else:
        print("Failed to fetch data from CoinMarketCap")
        print(response.status_code)
        return "Could not scrape CoinMarketCap"


def scrape_yt(url: str) -> dict:
    url = url.strip().replace("'", "").replace('"', "").replace("\n", "")
    print(f"Scraping YouTube: {url}")

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "writeinfojson": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        output = {
            "Description": "",
            "Transcript": "",
            "Average Retention": -1,
        }

        info = ydl.extract_info(url, download=False)
        output["Description"] = info.get("description", "No description available.")

        sponsor_segments = []
        try:
            response = requests.get(
                f"https://sponsor.ajay.app/api/skipSegments?videoID={info['id']}&category=sponsor"
            )
            if response.status_code == 200:
                sponsor_segments = response.json()
        except Exception:
            sponsor_segments = []

        retention_data = info.get("heatmap")
        if retention_data:
            non_sponsor_retention = []
            for retention in retention_data:
                time_start = retention["start_time"]
                time_end = retention["end_time"]

                is_sponsor = any(
                    seg["segment"][0] <= time_start <= seg["segment"][1]
                    or seg["segment"][0] <= time_end <= seg["segment"][1]
                    for seg in sponsor_segments
                )

                if not is_sponsor:
                    non_sponsor_retention.append(retention["value"])

            if non_sponsor_retention:
                output["Average Retention"] = round(np.mean(non_sponsor_retention), 4)

        try:
            subs = info.get("requested_subtitles", {})
            en_sub = subs.get("en") or subs.get("en-auto")
            if en_sub:
                subtitle_url = en_sub["url"]
                subtitle_content = ydl.urlopen(subtitle_url).read().decode("utf-8")
                vtt = webvtt.read_buffer(io.StringIO(subtitle_content))

                transcript_lines = []
                for caption in vtt:
                    is_sponsor = any(
                        seg["segment"][0]
                        <= caption.start_in_seconds
                        <= seg["segment"][1]
                        or seg["segment"][0]
                        <= caption.end_in_seconds
                        <= seg["segment"][1]
                        for seg in sponsor_segments
                    )

                    if not is_sponsor:
                        transcript_lines.append(caption.text.strip())

                output["Transcript"] = (
                    " ".join(transcript_lines)
                    if transcript_lines
                    else "No transcript available."
                )
            else:
                output["Transcript"] = "No English subtitles available."
        except Exception:
            output["Transcript"] = "Error extracting transcript."

        return output


def scrape_reddit(subreddit: str) -> dict:
    subreddit = subreddit.strip().replace("\n", "").replace("'", "").replace('"', "")
    print(f"Scraping Subreddit: {subreddit}")
    url = f"https://www.reddit.com/r/{subreddit}/best/.json"

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

    overall_sentiment = 0
    for i in output:
        url = output[i]["url"] + ".json"
        r = requests.get(url=url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            try:
                for j in range(10):
                    sentiment = label_map[
                        predict_sentiment(
                            data[1]["data"]["children"][j]["data"]["body"]
                        )
                    ]
                    output[i]["comments"][j] = {
                        "text": data[1]["data"]["children"][j]["data"]["body"],
                        "sentiment": sentiment,
                        "upvotes": data[1]["data"]["children"][j]["data"]["ups"],
                        "replies": {},
                    }
                    if sentiment == "Positive":
                        overall_sentiment += 1
                    else:
                        overall_sentiment -= 1
                    try:
                        for k in range(5):
                            sentiment = label_map[
                                predict_sentiment(
                                    data[1]["data"]["children"][j]["data"]["replies"]
                                )
                            ]
                            output[i]["comments"][j]["replies"][k] = {
                                "text": data[1]["data"]["children"][j]["data"][
                                    "replies"
                                ]["data"]["children"][k]["data"]["body"],
                                "sentiment": sentiment,
                                "upvotes": data[1]["data"]["children"][j]["data"][
                                    "replies"
                                ]["data"]["children"][k]["data"]["ups"],
                            }
                            if sentiment == "Positive":
                                overall_sentiment += 1
                            else:
                                overall_sentiment -= 1
                    except:
                        pass
            except Exception as e:
                pass
        else:
            print("Failed to fetch comments from Reddit")
            print(r.status_code)
    output["sentiment"] = overall_sentiment
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
            result = ydl.extract_info(f"ytsearch50:{search_query}", download=False)
            videos = result.get("entries", [])

            sorted_videos = sorted(
                videos, key=lambda x: x.get("view_count", 0), reverse=True
            )

            top_videos = {
                i: {
                    "title": video.get("title", "Unknown"),
                    "url": video.get("url", ""),
                }
                for i, video in enumerate(sorted_videos[:6])
            }

            return top_videos
    except Exception as e:
        return {"error": str(e)}


def getArticles(search_query: str) -> dict:
    url = f"https://serpapi.com/search?engine=google_news&q={search_query}&api_key={os.getenv('SERP_API_KEY')}"
    response = requests.get(url)

    if response.status_code != 200:
        print("Failed to fetch data from Google News")
        print(response.status_code)
        return {"error": "Could not scrape Google News"}

    data = response.json()
    articles = data.get("news_results", [])
    results = {}

    count = 0
    for i, article in enumerate(articles):
        if count >= 5:
            break

        link = article.get("link")
        if not link:
            continue

        if link.startswith("https://fortune.com"):
            continue

        config = Config()
        config.request_timeout = 2

        try:
            print(f"Scraping Article: {link}")
            news_article = Article(link, config=config)
            news_article.download()
            news_article.parse()
            news_article.nlp()

            if not news_article.title or not news_article.summary:
                print("Failed to parse article")
                continue

            results[count] = {
                "title": news_article.title,
                "link": link,
                "text": news_article.text,
                "summary": news_article.summary,
                "keywords": news_article.keywords,
            }

            count += 1
        except Exception as e:
            print(f"Failed to scrape article {link}")
            continue

    return results


yt_cache = {}
reddit_cache = {}
articles_cache = {}
coinmarketcap_cache = {}
summary_cache = {}


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Welcome to the DeepCoin API"})


@app.route("/v1/scrapeYoutube", methods=["POST"])
def scrapeYoutube():
    coin = request.json["coin"]

    if coin in yt_cache:
        print(f"Returning cached data for {coin}")
        return jsonify(yt_cache[coin])

    search_query = coin + " latest news"
    videos = search_youtube(search_query=search_query)

    for key, video in videos.items():
        try:
            link = video.get("url")
            if link.startswith("https://www.youtube.com/shorts") or not link:
                continue
            scraped_data = scrape_yt(link)
            video.update(scraped_data)
        except Exception as e:
            print(f"Error scraping video {link}: {e}")
            video["scrape_error"] = str(e)

    yt_cache[coin] = videos

    return jsonify(videos)


@app.route("/v1/scrapeReddit", methods=["POST"])
def scrapeReddit():
    coin = request.json.get("coin")

    if coin in reddit_cache:
        print(f"Returning cached data for {coin}")
        return json.dumps(reddit_cache[coin])

    try:
        posts = scrape_reddit(coin)
        reddit_cache[coin] = posts
        return json.dumps(posts)
    except Exception as e:
        print(f"Error scraping Reddit for {coin}: {e}")
        return jsonify({"error": "Failed to scrape Reddit"}), 500


@app.route("/v1/scrapeArticles", methods=["POST"])
def scrapeArticles():
    coin = request.json.get("coin")

    if coin in articles_cache:
        print(f"Returning cached data for {coin}")
        return jsonify(articles_cache[coin])

    try:
        data = getArticles(coin)
        articles_cache[coin] = data
        return jsonify(data)
    except Exception as e:
        print(f"Error fetching coin analysis for {coin}: {e}")
        return jsonify({"error": "Failed to fetch coin analysis"}), 500


@app.route("/v1/scrapeCoinMarketCap", methods=["POST"])
def scrapeCoinMarketCap():
    return (
        "Scraping CoinMarketCap is not supported directly. Use /v1/analyzeCoin instead."
    )


@app.route("/v1/analyzeCoin", methods=["POST"])
def analyzeCoin():
    coin = request.json.get("coin")

    if coin in summary_cache:
        print(f"Returning cached data for {coin}")
        return jsonify(summary_cache[coin])

    try:
        if coin in coinmarketcap_cache:
            print(f"Returning cached data for {coin}")
            coinmarketcap_data = coinmarketcap_cache[coin]
        else:
            coinmarketcap_data = scrape_coinmarketcap(coin)
            coinmarketcap_cache[coin] = coinmarketcap_data
    except Exception as e:
        print(f"Error scraping CoinMarketCap for {coin}")
        coinmarketcap_data = {"error": "Failed to scrape CoinMarketCap"}

    try:
        if coin in reddit_cache:
            print(f"Returning cached data for {coin}")
            reddit_data = reddit_cache[coin]
        else:
            reddit_data = scrape_reddit(coin)
            reddit_cache[coin] = reddit_data
    except Exception as e:
        print(f"Error scraping Reddit for {coin}")
        reddit_data = {"error": "Failed to scrape Reddit"}

    try:
        if coin in articles_cache:
            print(f"Returning cached data for {coin}")
            articles_data = articles_cache[coin]
        else:
            articles_data = getArticles(coin)
            articles_cache[coin] = articles_data
    except Exception as e:
        print(f"Error fetching articles for {coin}")
        articles_data = {"error": "Failed to fetch articles"}

    try:
        if coin in yt_cache:
            print(f"Returning cached data for {coin}")
            youtube_data = yt_cache[coin]
        else:
            search_query = coin + " latest news"
            youtube_data = search_youtube(search_query=search_query)
            yt_cache[coin] = youtube_data

            for key, video in youtube_data.items():
                try:
                    link = video.get("url")
                    if not link:
                        continue
                    scraped_data = scrape_yt(link)
                    video.update(scraped_data)
                except Exception as e:
                    print(f"Error scraping video {link}: {e}")
                    video["scrape_error"] = str(e)
    except Exception as e:
        print(f"Error searching YouTube for {coin}")
        youtube_data = {"error": "Failed to search YouTube"}

    prompt = f"""
    You are an expert crypto analyst. Given the following data, analyze the coin and provide a summary of the current state of the coin, its potential future, and any other relevant insights.
    Output the analysis as a plain text. And give only the analysis, no other text or any other line, start from the analysis. You can using full markdown.
    Reddit Data: {reddit_data}
    Articles Data: {articles_data}
    YouTube Data: {youtube_data}
    """
    response = geminiClient.models.generate_content(
        model="gemini-2.0-flash", contents=prompt
    )

    output = {"analysis": response.text}
    summary_cache[coin] = output
    return jsonify(output)


if __name__ == "__main__":
    app.run(debug=os.getenv("DEBUG", False), host="0.0.0.0", port=8000)
