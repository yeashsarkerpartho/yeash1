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
    """Sanitizes category name to be used as a valid filename."""
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(category_name))
    safe_name = re.sub(r'_+', '_', safe_name).strip('_')
    return f"{safe_name}.json" if safe_name else "Others.json"

def get_next_data_json(html_content):
    """Extracts the __NEXT_DATA__ JSON object from Next.js HTML."""
    if not html_content:
        return None
    pattern = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
    match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None

def send_log(message, msg_type="INFO"):
    """Prints colored log messages to the console."""
    color_code = "\033[97m" # Default White
    if msg_type == "ERROR":
        color_code = "\033[91m" # Red
    elif msg_type == "SUCCESS":
        color_code = "\033[92m" # Green
    elif msg_type == "WARNING":
        color_code = "\033[93m" # Yellow
    reset_code = "\033[0m"
    print(f"{color_code}[{msg_type}] {message}{reset_code}")
    sys.stdout.flush()

async def fetch_page(session, url, referer="https://www.google.com/", retries=3):
    """Fetches a web page with retry mechanism to prevent missing data."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Referer': referer,
        'Connection': 'keep-alive',
    }
    
    for attempt in range(1, retries + 1):
        try:
            # Increased timeout to 30 seconds to handle slow server responses
            async with session.get(url, headers=headers, timeout=30, ssl=False) as response:
                if response.status == 200:
                    return await response.text()
                elif response.status in [404, 403]:
                    # Unlikely to recover from 404 or 403, don't retry
                    return None
        except Exception as e:
            if attempt == retries:
                send_log(f"Failed to fetch {url} after {retries} attempts. Error: {str(e)}", "ERROR")
                return None
            await asyncio.sleep(1.5) # Wait before retrying
    return None

async def fetch_multiple(session, urls_dict):
    """Fetches multiple URLs concurrently."""
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
    """Loads scraping progress from file if it exists."""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {
        'currentPage': 1,
        'currentMovieIndex': 0,
        'moviesData': []
    }

def save_progress(current_page, current_index, movies_data):
    """Saves current scraping state to avoid data loss on crash."""
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'currentPage': current_page,
            'currentMovieIndex': current_index,
            'moviesData': movies_data
        }, f, indent=4)

def process_movie_details(movie_id, detail_html, poster_url):
    """Parses individual movie page data."""
    detail_data = get_next_data_json(detail_html)
    if not detail_data:
        return None
        
    page_props = detail_data.get('props', {}).get('pageProps', {})
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
        
    # LOGIC UPDATE: Handle missing categories by assigning them to 'Others'
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

async def main():
    progress = load_progress()
    page = progress['currentPage']
    start_index = progress['currentMovieIndex']
    final_movies_data = progress['moviesData']
    
    async with aiohttp.ClientSession() as session:
        if page > 1 or start_index > 0:
            send_log(f"Resuming from page {page} (Index: {start_index})...", "SUCCESS")
        else:
            send_log("Starting scraping process...", "INFO")

        # LOGIC UPDATE: Infinite loop for dynamic page fetching
        while True:
            send_log(f"\n--- Fetching Page: {page} ---", "INFO")
            page_url = f"{MOVIES_LIST_URL}?page={page}"
            
            list_html = await fetch_page(session, page_url)
            
            if not list_html:
                send_log(f"No response from page {page}. Assuming end of pagination or block.", "WARNING")
                break # Stop loop if page fails to load completely after retries
                
            list_data = get_next_data_json(list_html)
            if not list_data:
                send_log(f"No JSON data found on page {page}. Assuming end of pagination.", "INFO")
                break # Stop loop if no Next.js data is found
                
            movies_props = list_data.get('props', {}).get('pageProps', {}).get('movies', {})
            
            if isinstance(movies_props, dict):
                movies_array = movies_props.get('data', movies_props)
            elif isinstance(movies_props, list):
                movies_array = movies_props
            else:
                movies_array = []
                
            # LOGIC UPDATE: If array is empty, we reached the end of the data
            if not movies_array:
                send_log(f"Page {page} is empty. Reached the end of available movies.", "SUCCESS")
                break
                
            total_movies_in_page = len(movies_array)
            current_index = start_index if page == progress['currentPage'] else 0
            
            if current_index >= total_movies_in_page:
                page += 1
                start_index = 0
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
                send_log(f"Batch fetching movies {batch_start} to {batch_end} out of {total_movies_in_page} (Page {page})...")
                
                multi_responses = await fetch_multiple(session, urls_to_fetch)
                
                for m_id, detail_html in multi_responses.items():
                    if not detail_html:
                        send_log(f"Data missing for Movie ID: {m_id} (Skipping)", "ERROR")
                        continue
                        
                    formatted_movie = process_movie_details(m_id, detail_html, movie_posters.get(m_id, ""))
                    
                    if formatted_movie:
                        existing_idx = next((i for i, item in enumerate(final_movies_data) if item.get("id") == m_id), -1)
                        if existing_idx >= 0:
                            final_movies_data[existing_idx] = formatted_movie
                        else:
                            final_movies_data.append(formatted_movie)
                            
                current_index += len(batch)
                save_progress(page, current_index, final_movies_data)
                await asyncio.sleep(0.3)
                
            send_log(f"Successfully processed page {page}.", "SUCCESS")
            
            # Move to next page
            page += 1
            start_index = 0
            save_progress(page, start_index, final_movies_data)
            await asyncio.sleep(0.5)
            
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
            
        # Clean up progress file since we finished successfully
        if os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)

if __name__ == "__main__":
    asyncio.run(main())
