import json
import re
import os
import sys
import socket
import time
import requests
import urllib3.util.connection as urllib3_cn
from concurrent.futures import ThreadPoolExecutor, as_completed

# CRITICAL FIX: Force IPv4 globally to completely bypass GitHub Actions timeout/hanging
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

BASE_URL = "https://m.mymoviebazar.net"
MOVIES_LIST_URL = f"{BASE_URL}/movies"
PROGRESS_FILE = 'progress.json'

def get_safe_filename(category_name):
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(category_name))
    safe_name = re.sub(r'_+', '_', safe_name).strip('_')
    return f"{safe_name}.json" if safe_name else "Others.json"

def send_log(message, msg_type="INFO"):
    color_code = "\033[97m"
    if msg_type == "ERROR":
        color_code = "\033[91m"
    elif msg_type == "SUCCESS":
        color_code = "\033[92m"
    elif msg_type == "WARNING":
        color_code = "\033[93m"
    reset_code = "\033[0m"
    print(f"{color_code}[{msg_type}] {message}{reset_code}")
    sys.stdout.flush()

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {'currentPage': 1, 'currentMovieIndex': 0, 'moviesData': []}

def save_progress(current_page, current_index, movies_data):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'currentPage': current_page,
            'currentMovieIndex': current_index,
            'moviesData': movies_data
        }, f, indent=4)

def get_headers(referer_url=None, is_json_api=True):
    """Generates exact Chrome 154 headers based on user's cURL data"""
    headers = {
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"'
    }
    
    if referer_url:
        headers['Referer'] = referer_url

    if is_json_api:
        headers['Accept'] = '*/*'
        headers['Sec-Fetch-Dest'] = 'empty'
        headers['Sec-Fetch-Mode'] = 'cors'
        headers['Sec-Fetch-Site'] = 'same-origin'
        headers['x-nextjs-data'] = '1'
    else:
        headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
        headers['Sec-Fetch-Dest'] = 'document'
        headers['Sec-Fetch-Mode'] = 'navigate'
        headers['Sec-Fetch-Site'] = 'none'

    return headers

def get_build_id(session):
    headers = get_headers(is_json_api=False)
    try:
        response = session.get(MOVIES_LIST_URL, headers=headers, timeout=30)
        if response.status_code == 200:
            match = re.search(r'"buildId":"([^"]+)"', response.text)
            if match:
                return match.group(1)
    except Exception as e:
        send_log(f"Failed to dynamically fetch Build ID: {repr(e)}", "WARNING")
    
    return "0LbgtP84amlc5Y40vjZrV"

def fetch_json_api(session, build_id, page):
    api_url = f"{BASE_URL}/_next/data/{build_id}/movies.json"
    referer_url = f"{BASE_URL}/movies"
    if page > 1:
        api_url += f"?page={page}"
        referer_url += f"?page={page}"
        
    headers = get_headers(referer_url=referer_url, is_json_api=True)
    
    for attempt in range(1, 4):
        try:
            response = session.get(api_url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            elif response.status_code in [404, 403]:
                return None
        except Exception as e:
            if attempt == 3:
                # Using repr(e) to expose hidden connection drop errors
                send_log(f"API Fetch failed for page {page}: {repr(e)}", "ERROR")
        time.sleep(1.5)
    return None

def fetch_movie_detail_json(session, build_id, movie_id):
    api_url = f"{BASE_URL}/_next/data/{build_id}/movies/watch/{movie_id}.json"
    headers = get_headers(referer_url=f"{BASE_URL}/movies", is_json_api=True)
    
    try:
        response = session.get(api_url, headers=headers, timeout=20)
        if response.status_code == 200:
            return movie_id, response.json()
    except Exception:
        pass
    return movie_id, None

def fetch_multiple_details(session, build_id, urls_dict):
    """Fetches details concurrently using ThreadPoolExecutor for speed"""
    results = {}
    keys = list(urls_dict.keys())
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_movie_detail_json, session, build_id, m_id) for m_id in keys]
        for future in as_completed(futures):
            m_id, data = future.result()
            results[m_id] = data
            
    return results

def process_movie_details(movie_id, detail_json, poster_url):
    if not detail_json: return None
        
    page_props = detail_json.get('pageProps', {})
    movie_details = page_props.get('movie', page_props)
    
    if not movie_details or not isinstance(movie_details, dict):
        return None
    
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
        genres = [genres] if genres else ["Unknown"]
        
    category = movie_details.get('type') or movie_details.get('category')
    if not category or str(category).strip() == "":
        category = "Others"
        
    storyline = movie_details.get('plot') or movie_details.get('description') or ""
    
    return {
        "id": movie_id,
        "category": str(category).strip(),
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
        "streamUrl": f"{BASE_URL}/api/movies/watch/{movie_id}",
        "title": title,
        "headers": {
            "referer": f"{BASE_URL}/",
            "origin": "",
            "user_agent": ""
        }
    }

def main():
    progress = load_progress()
    page = progress['currentPage']
    start_index = progress['currentMovieIndex']
    final_movies_data = progress['moviesData']
    
    with requests.Session() as session:
        send_log("Fetching Next.js Build ID to use direct API...", "INFO")
        build_id = get_build_id(session)
        send_log(f"Using Direct JSON API with Build ID: {build_id}", "SUCCESS")

        if page > 1 or start_index > 0:
            send_log(f"Resuming from page {page} (Index: {start_index})...", "SUCCESS")

        while True:
            send_log(f"\n--- Fetching Page: {page} via Next.js API ---", "INFO")
            
            list_data = fetch_json_api(session, build_id, page)
            
            if not list_data:
                send_log(f"No response from page {page}. Assuming end of pagination.", "WARNING")
                break 
                
            movies_props = list_data.get('pageProps', {}).get('movies', {})
            
            if isinstance(movies_props, dict):
                movies_array = movies_props.get('data', movies_props)
            elif isinstance(movies_props, list):
                movies_array = movies_props
            else:
                movies_array = []
                
            if not movies_array:
                send_log(f"Page {page} API returned empty array. Reached the end.", "SUCCESS")
                break
                
            total_movies_in_page = len(movies_array)
            current_index = start_index if page == progress['currentPage'] else 0
            
            if current_index >= total_movies_in_page:
                page += 1
                start_index = 0
                continue
                
            batch_size = 5
            remaining_movies = movies_array[current_index:]
            batches = [remaining_movies[i:i + batch_size] for i in range(0, len(remaining_movies), batch_size)]
            
            for batch in batches:
                urls_to_fetch = {}
                movie_posters = {}
                
                for movie in batch:
                    movie_id = movie.get('id')
                    if not movie_id: continue
                    movie_posters[movie_id] = movie.get('image_link', '')
                    urls_to_fetch[movie_id] = True 
                    
                batch_start = current_index + 1
                batch_end = current_index + len(batch)
                send_log(f"Batch fetching JSON for movies {batch_start} to {batch_end} (Page {page})...")
                
                multi_responses = fetch_multiple_details(session, build_id, urls_to_fetch)
                
                for m_id, detail_json in multi_responses.items():
                    if not detail_json:
                        send_log(f"JSON missing for Movie ID: {m_id} (Skipping)", "ERROR")
                        continue
                        
                    formatted_movie = process_movie_details(m_id, detail_json, movie_posters.get(m_id, ""))
                    
                    if formatted_movie:
                        existing_idx = next((i for i, item in enumerate(final_movies_data) if item.get("id") == m_id), -1)
                        if existing_idx >= 0:
                            final_movies_data[existing_idx] = formatted_movie
                        else:
                            final_movies_data.append(formatted_movie)
                            
                current_index += len(batch)
                save_progress(page, current_index, final_movies_data)
                time.sleep(0.3)
                
            send_log(f"Successfully processed page {page}.", "SUCCESS")
            
            page += 1
            start_index = 0
            save_progress(page, start_index, final_movies_data)
            time.sleep(0.5)
            
        send_log("Scraping completed! Organizing data...", "SUCCESS")
        
        categorized_data = {}
        for movie in final_movies_data:
            cat = movie.get('category', 'Others')
            if not cat or cat.strip() == "":
                cat = 'Others'
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
    main()
