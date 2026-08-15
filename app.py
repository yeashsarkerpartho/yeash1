import asyncio
import aiohttp
import json
import re
import os
import sys

BASE_URL = "https://m.mymoviebazar.net"
MOVIES_LIST_URL = f"{BASE_URL}/movies"
PROGRESS_FILE = 'progress.json'

def get_safe_filename(category_name):
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', category_name)
    safe_name = re.sub(r'_+', '_', safe_name).strip('_')
    return f"{safe_name}.json" if safe_name else "Uncategorized.json"

def get_next_data_json(html_content):
    pattern = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
    match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None

def send_log(message, msg_type="INFO"):
    color_code = "\033[97m"
    if msg_type == "ERROR":
        color_code = "\033[91m"
    elif msg_type == "SUCCESS":
        color_code = "\033[92m"
    reset_code = "\033[0m"
    print(f"{color_code}[{msg_type}] {message}{reset_code}")
    sys.stdout.flush()

async def fetch_page(session, url, referer="https://www.google.com/"):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Referer': referer,
        'Connection': 'keep-alive',
    }
    try:
        async with session.get(url, headers=headers, timeout=20, ssl=False) as response:
            if response.status == 200:
                return await response.text()
            return None
    except Exception:
        return None

async def fetch_multiple(session, urls_dict):
    tasks = []
    for movie_id, url in urls_dict.items():
        tasks.append(fetch_page(session, url, referer=BASE_URL))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    final_results = {}
    for (movie_id, _), html in zip(urls_dict.items(), results):
        if isinstance(html, Exception):
            final_results[movie_id] = None
        else:
            final_results[movie_id] = html
    return final_results

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {
        'currentPage': 1,
        'currentMovieIndex': 0,
        'totalPages': 1,
        'moviesData': []
    }

def save_progress(current_page, current_index, total_pages, movies_data):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'currentPage': current_page,
            'currentMovieIndex': current_index,
            'totalPages': total_pages,
            'moviesData': movies_data
        }, f, indent=4)

def process_movie_details(movie_id, detail_html, poster_url):
    detail_data = get_next_data_json(detail_html)
    if not detail_data:
        return None
        
    page_props = detail_data.get('props', {}).get('pageProps', {})
    movie_details = page_props.get('movie', page_props)
    
    director = movie_details.get('director_name') or movie_details.get('director') or "Unknown"
    
    release_date = movie_details.get('release_date') or str(movie_details.get('release_year', ''))
    release_year = ""
    year_match = re.search(r'\b(19|20)\d{2}\b', str(release_date))
    if year_match:
        release_year = year_match.group(0)
        
    raw_title = movie_details.get('title', 'Unknown Title')
    title = raw_title
    if release_year and release_year not in raw_title:
        title = f"{raw_title.strip()} ({release_year})"
        
    genres = movie_details.get('genres', ["Unknown"])
    if not isinstance(genres, list):
        genres = [genres]
        
    category = movie_details.get('type') or movie_details.get('category') or "Movies"
        
    storyline = movie_details.get('plot') or movie_details.get('description') or ""
    
    return {
        "id": movie_id,
        "category": category,
        "director": director,
        "genre": genres,
        "imdbRating": str(movie_details.get('imdb_rating', '0.0')),
        "imdbVotes": 0,
        "language": movie_details.get('language', 'Unknown'),
        "posterUrl": poster_url,
        "releaseDate": release_date,
        "sliderUrl": movie_details.get('backdrop') or poster_url,
        "status": "on",
        "storyline": storyline,
        "streamUrl": f"{BASE_URL}/movies/watch/{movie_id}",
        "title": title,
        "headers": {
            "referer": f"{BASE_URL}/",
            "origin": "",
            "user_agent": ""
        }
    }

async def main():
    progress = load_progress()
    start_page = progress['currentPage']
    start_index = progress['currentMovieIndex']
    total_pages = progress['totalPages']
    final_movies_data = progress['moviesData']
    
    async with aiohttp.ClientSession() as session:
        if start_page == 1 and start_index == 0:
            send_log("Initializing request to fetch total pages...", "INFO")
            first_page_html = await fetch_page(session, f"{MOVIES_LIST_URL}?page=1")
            first_page_data = get_next_data_json(first_page_html) if first_page_html else None
            
            if first_page_data:
                movie_props = first_page_data.get('props', {}).get('pageProps', {}).get('movies', {})
                if isinstance(movie_props, dict):
                    if 'last_page' in movie_props:
                        total_pages = movie_props['last_page']
                    elif 'meta' in movie_props and 'last_page' in movie_props['meta']:
                        total_pages = movie_props['meta']['last_page']
                    else:
                        total_pages = 175
                else:
                    total_pages = 175
            
            send_log(f"Total pages found: {total_pages}", "SUCCESS")
            save_progress(1, 0, total_pages, final_movies_data)
        else:
            send_log(f"Resuming from page {start_page} (Index: {start_index}) of {total_pages}...", "SUCCESS")

        for page in range(start_page, total_pages + 1):
            send_log(f"\n--- Fetching Page: {page} ---", "INFO")
            page_url = f"{MOVIES_LIST_URL}?page={page}"
            list_html = await fetch_page(session, page_url)
            
            if not list_html:
                send_log(f"Failed to get data for page {page}.", "ERROR")
                sys.exit(1)
                
            list_data = get_next_data_json(list_html)
            if not list_data:
                send_log(f"No JSON data found on page {page}.", "ERROR")
                sys.exit(1)
                
            movies_props = list_data.get('props', {}).get('pageProps', {}).get('movies', {})
            
            if isinstance(movies_props, dict):
                movies_array = movies_props.get('data', movies_props)
            elif isinstance(movies_props, list):
                movies_array = movies_props
            else:
                movies_array = []
                
            if not movies_array:
                send_log(f"Page {page} is empty. Reached the end.", "INFO")
                break
                
            total_movies_in_page = len(movies_array)
            current_index = start_index if page == start_page else 0
            
            if current_index >= total_movies_in_page:
                continue
                
            batch_size = 4
            remaining_movies = movies_array[current_index:]
            batches = [remaining_movies[i:i + batch_size] for i in range(0, len(remaining_movies), batch_size)]
            
            for batch in batches:
                urls_to_fetch = {}
                movie_posters = {}
                
                for movie in batch:
                    movie_id = movie.get('id')
                    if not movie_id:
                        continue
                    movie_posters[movie_id] = movie.get('image_link', '')
                    urls_to_fetch[movie_id] = f"{BASE_URL}/movies/watch/{movie_id}"
                    
                batch_start = current_index + 1
                batch_end = current_index + len(batch)
                send_log(f"Batch fetching movies {batch_start} to {batch_end} out of {total_movies_in_page}...")
                
                multi_responses = await fetch_multiple(session, urls_to_fetch)
                
                for m_id, detail_html in multi_responses.items():
                    if not detail_html:
                        continue
                    formatted_movie = process_movie_details(m_id, detail_html, movie_posters.get(m_id, ""))
                    if formatted_movie:
                        existing_idx = next((i for i, item in enumerate(final_movies_data) if item.get("id") == m_id), -1)
                        if existing_idx >= 0:
                            final_movies_data[existing_idx] = formatted_movie
                        else:
                            final_movies_data.append(formatted_movie)
                            
                current_index += len(batch)
                save_progress(page, current_index, total_pages, final_movies_data)
                await asyncio.sleep(0.3)
                
            send_log(f"Successfully processed page {page}.", "SUCCESS")
            save_progress(page + 1, 0, total_pages, final_movies_data)
            await asyncio.sleep(0.5)
            
        send_log("All pages processed successfully!", "SUCCESS")
        
        categorized_data = {}
        for movie in final_movies_data:
            cat = movie.get('category', 'Uncategorized')
            if cat not in categorized_data:
                categorized_data[cat] = []
            categorized_data[cat].append(movie)
            
        for cat, movies in categorized_data.items():
            filename = get_safe_filename(cat)
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(movies, f, indent=4, ensure_ascii=False)
            send_log(f"Saved {len(movies)} movies to {filename}", "SUCCESS")
            
        if os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)

if __name__ == "__main__":
    asyncio.run(main())
