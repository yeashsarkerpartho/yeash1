import asyncio
import aiohttp
import json
import os
import re
import sys

BASE_URL = "https://m.mymoviebazar.net"
MOVIES_LIST_URL = f"{BASE_URL}/movies"
PROGRESS_FILE = 'progress.json'
BATCH_SIZE = 4

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Connection': 'keep-alive',
    'Referer': 'https://www.google.com/'
}

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    RESET = '\033[0m'

def log(message, level="info"):
    if level == "error":
        print(f"{Colors.RED}[ERROR] {message}{Colors.RESET}")
    elif level == "success":
        print(f"{Colors.GREEN}[SUCCESS] {message}{Colors.RESET}")
    elif level == "warn":
        print(f"{Colors.YELLOW}[WARN] {message}{Colors.RESET}")
    else:
        print(f"{Colors.CYAN}[INFO] {message}{Colors.RESET}")

async def fetch_webpage(session, url, retries=3):
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=15) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    log(f"Received status code {response.status} for {url}", "error")
        except asyncio.TimeoutError:
            log(f"Timeout while fetching {url}. Attempt {attempt + 1}/{retries}", "warn")
        except Exception as e:
            log(f"Error fetching {url}: {e}", "error")
        
        await asyncio.sleep(2)
    return None

def get_next_data_json(html_content):
    if not html_content:
        return None
    
    pattern = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL | re.IGNORECASE)
    match = pattern.search(html_content)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None

def get_safe_filename(category_string):
    if not category_string:
        return "Unknown_Category"
    safe_name = re.sub(r'[^A-Za-z0-9]+', '_', str(category_string).strip())
    return safe_name.strip('_')

async def process_movie(session, movie, movie_index):
    movie_id = movie.get('id')
    poster_url = movie.get('image_link', '')
    detail_url = f"{BASE_URL}/movies/watch/{movie_id}"
    
    html = await fetch_webpage(session, detail_url)
    if not html:
        return None

    detail_data = get_next_data_json(html)
    if not detail_data or 'pageProps' not in detail_data.get('props', {}):
        return None
    
    props = detail_data['props']['pageProps']
    movie_details = props.get('movie', props)
    
    director = "Unknown"
    if movie_details.get('director_name'):
        director = movie_details['director_name']
    elif movie_details.get('director'):
        director = movie_details['director']
        
    release_date = ""
    release_year = ""
    
    if movie_details.get('release_date'):
        release_date = str(movie_details['release_date'])
        year_match = re.search(r'\b(19|20)\d{2}\b', release_date)
        if year_match:
            release_year = year_match.group(0)
    elif movie_details.get('release_year'):
        release_date = str(movie_details['release_year'])
        release_year = release_date

    raw_title = movie_details.get('title', 'Unknown Title')
    title = raw_title
    if release_year and release_year not in raw_title:
        title = f"{raw_title.strip()} ({release_year})"
        
    genres = movie_details.get('genres', ["Unknown"])
    if not isinstance(genres, list):
        genres = ["Unknown"]
        
    category_name = "Movies"
    if movie_details.get('category_name'):
        category_name = movie_details['category_name']
    elif isinstance(movie_details.get('category'), dict) and movie_details['category'].get('name'):
        category_name = movie_details['category']['name']
    elif isinstance(movie_details.get('category'), str):
        category_name = movie_details['category']
    else:
        category_name = movie_details.get('type', 'Movies')
        
    storyline = movie_details.get('plot', movie_details.get('description', ''))

    formatted_movie = {
        "id": movie_id,
        "category": category_name,
        "director": director,
        "genre": genres,
        "imdbRating": str(movie_details.get('imdb_rating', '0.0')),
        "imdbVotes": 0,
        "language": movie_details.get('language', 'Unknown'),
        "posterUrl": poster_url,
        "releaseDate": release_date,
        "sliderUrl": movie_details.get('backdrop', poster_url),
        "status": "on",
        "storyline": storyline,
        "streamUrl": f"{BASE_URL}/movies/watch/{movie_id}",
        "title": title,
        "headers": {
            "referer": "https://m.mymoviebazar.net/",
            "origin": "",
            "user_agent": ""
        }
    }
    
    return formatted_movie

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                log(f"Resuming progress: Page {data['currentPage']}, Index {data['currentMovieIndex']}", "success")
                return data
        except Exception as e:
            log(f"Failed to read progress file: {e}", "error")
    return None

def save_progress(current_page, current_index, total_pages, movies_data):
    data = {
        "currentPage": current_page,
        "currentMovieIndex": current_index,
        "totalPages": total_pages,
        "moviesData": movies_data
    }
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def update_or_append_movie(movies_list, new_movie):
    for i, existing_movie in enumerate(movies_list):
        if existing_movie.get('id') == new_movie.get('id'):
            movies_list[i] = new_movie
            return
    movies_list.append(new_movie)

async def main():
    print(f"\n{Colors.YELLOW}======================================")
    print(f"   Fast Resumable Movie Scraper v2.0")
    print(f"======================================{Colors.RESET}\n")

    start_page = 1
    start_movie_index = 0
    total_pages = 1
    final_movies_data = []

    progress_data = load_progress()
    if progress_data:
        start_page = progress_data.get('currentPage', 1)
        start_movie_index = progress_data.get('currentMovieIndex', 0)
        total_pages = progress_data.get('totalPages', 1)
        final_movies_data = progress_data.get('moviesData', [])

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        
        if start_page == 1 and not progress_data:
            log("Initializing request to fetch total pages...")
            first_page_html = await fetch_webpage(session, f"{MOVIES_LIST_URL}?page=1")
            first_page_data = get_next_data_json(first_page_html)
            
            if first_page_data and 'movies' in first_page_data.get('props', {}).get('pageProps', {}):
                movie_props = first_page_data['props']['pageProps']['movies']
                if 'last_page' in movie_props:
                    total_pages = movie_props['last_page']
                elif 'meta' in movie_props and 'last_page' in movie_props['meta']:
                    total_pages = movie_props['meta']['last_page']
                else:
                    total_pages = 175
                    
            log(f"Total pages found: {total_pages}", "success")
            save_progress(1, 0, total_pages, final_movies_data)

        for page in range(start_page, total_pages + 1):
            log(f"\n--- Fetching Page: {page} ---")
            
            page_url = f"{MOVIES_LIST_URL}?page={page}"
            list_html = await fetch_webpage(session, page_url)
            list_data = get_next_data_json(list_html)
            
            if not list_data or 'movies' not in list_data.get('props', {}).get('pageProps', {}):
                log(f"Failed to get data for page {page}. Retrying in 5 seconds...", "error")
                await asyncio.sleep(5)
                continue 
            
            movies_props = list_data['props']['pageProps']['movies']
            movies_array = movies_props.get('data', movies_props)
            
            if not movies_array:
                log(f"Page {page} is empty. Reached the end.")
                break

            total_movies_in_page = len(movies_array)
            current_index = start_movie_index if page == start_page else 0

            if current_index >= total_movies_in_page:
                log(f"All movies in page {page} already fetched.")
                continue
                
            log(f"Page {page}: Processing from movie index {current_index} out of {total_movies_in_page}...")

            while current_index < total_movies_in_page:
                batch = movies_array[current_index : current_index + BATCH_SIZE]
                
                batch_start = current_index + 1
                batch_end = current_index + len(batch)
                log(f"Batch fetching movies {batch_start} to {batch_end}...")
                
                tasks = [process_movie(session, movie, idx) for idx, movie in enumerate(batch)]
                results = await asyncio.gather(*tasks)
                
                valid_movies = 0
                for result in results:
                    if result:
                        update_or_append_movie(final_movies_data, result)
                        valid_movies += 1
                        
                current_index += len(batch)
                
                save_progress(page, current_index, total_pages, final_movies_data)
                
                await asyncio.sleep(0.3)
            
            log(f"Successfully processed page {page}. Total Movies Fetched: {len(final_movies_data)}", "success")
            
            if page < total_pages:
                save_progress(page + 1, 0, total_pages, final_movies_data)
                start_movie_index = 0

    log("\nAll pages processed successfully!", "success")
    
    categorized_movies = {}
    for movie_data in final_movies_data:
        cat_name = movie_data.get('category', 'Movies')
        safe_filename = get_safe_filename(cat_name)
        
        if safe_filename not in categorized_movies:
            categorized_movies[safe_filename] = []
        categorized_movies[safe_filename].append(movie_data)
        
    log("\n--- Saving Categorized Data ---")
    for file_name, movies in categorized_movies.items():
        json_file = f"{file_name}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(movies, f, ensure_ascii=False, indent=4)
        log(f"Saved {len(movies)} movies to {json_file}", "success")
    
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        log("Progress tracker cleaned up.", "info")

if __name__ == "__main__":
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n[WARN] Script interrupted by user! Don't worry, progress is saved in 'progress.json'. Run the script again to resume.")
