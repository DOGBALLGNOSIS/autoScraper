import os
import time
import random
import logging
import re
import json
from typing import List, Optional
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import sys
import openai
import pandas as pd
from webdriver_manager.chrome import ChromeDriverManager

# Directory initialization
FAILED_PAGES_DIR = 'failed_pages'
OUTPUT_DIR = 'scraped_data'
os.makedirs(FAILED_PAGES_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Custom logging handler for UTF-8
class UTF8StreamHandler(logging.StreamHandler):
    def __init__(self, stream=None):
        super().__init__(stream or sys.stdout)
        self.stream = stream or sys.stdout

    def emit(self, record):
        try:
            msg = self.format(record)
            self.stream.write(msg + self.terminator)
            self.flush()
        except UnicodeEncodeError:
            msg = msg.encode('utf-8', errors='replace').decode('utf-8')
            self.stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("scraper.log", encoding='utf-8'),
        UTF8StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Retrieve OpenAI API key from env
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.error("OpenAI API Key not set.")
    sys.exit(1)

# Config constants
PAGE_LOAD_TIMEOUT = 60
MAX_RETRIES = 3
DELAY_RANGE = (1, 3)

def setup_driver(headless: bool = True) -> Optional[webdriver.Chrome]:
    """Initialize Selenium WebDriver with options."""
    try:
        options = Options()
        if headless:
            options.add_argument('--headless=new')  # Headless mode
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-infobars')
        options.add_argument('--enable-unsafe-swiftshader')
        options.add_argument('--log-level=3')  # Suppress logs
        options.add_argument('--disable-webgpu')  # Disable WebGPU
        options.add_argument('--disable-software-rasterizer')  # Further GPU suppression
        options.add_argument(f'user-agent={'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'}') # Config user agent

        # Suppress ChromeDriver logs
        service = ChromeService(
            executable_path=ChromeDriverManager().install(),
            log_path=os.devnull
        )

        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        logger.info("WebDriver initialized.")
        return driver
    except WebDriverException as e:
        logger.error(f"WebDriver initialization failed: {e}")
        return None

def sanitize_filename(url: str) -> str:
    """Generate a safe filename from URL"""
    return re.sub(r'[\\/*?:"<>|]', "_", url.replace("https://", "").replace("http://", "").replace("/", "_"))

def save_failed_page(url: str, driver: webdriver.Chrome):
    """Save HTML of a failed page"""
    try:
        page_source = driver.page_source
        filename = os.path.join(FAILED_PAGES_DIR, f"{sanitize_filename(url)}.html")
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(page_source)
        logger.info(f"Saved failed page: {filename}")
    except Exception as ex:
        logger.error(f"Saving failed page for {url} failed: {ex}")

def get_browser_logs(driver: webdriver.Chrome):
    """Retrieve and log console msgs"""
    try:
        logs = driver.get_log('browser')
        for entry in logs:
            sanitized = sanitize_log_message(str(entry))
            logger.debug(f"Browser log: {sanitized}")
    except Exception as ex:
        logger.error(f"Retrieving browser logs failed: {ex}")

def sanitize_log_message(message: str) -> str:
    """Remove non-ASCII characters from log"""
    return re.sub(r'[^\x00-\x7F]+', '?', message)

def scroll_to_bottom(driver: webdriver.Chrome):
    """Scroll to the bottom to load dynamic content"""
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(1, 2))
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def extract_relevant_text(driver: webdriver.Chrome, url: str) -> Optional[str]:
    """Extract main textual content from the page"""
    try:
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        # Remove unnecessary tags
        for tag in soup(['script', 'style', 'noscript', 'header', 'footer', 'nav', 'aside', 'form', 'button', 'img', 'video', 'iframe']):
            tag.decompose()
        text_content = soup.get_text(separator='\n', strip=True)
        logger.info(f"Extracted text: {len(text_content)} characters.")
        return text_content
    except Exception as ex:
        logger.error(f"Extracting text from {url} failed: {ex}")
        save_failed_page(url, driver)
        return None

def scrape_with_selenium(url: str, driver: webdriver.Chrome) -> Optional[str]:
    """Navigate to the URL and scrape text"""
    try:
        logger.info(f"Navigating to {url}")
        driver.get(url)
        get_browser_logs(driver)
        scroll_to_bottom(driver)
        text_content = extract_relevant_text(driver, url)
        if not text_content:
            logger.error(f"No text extracted from {url}")
            save_failed_page(url, driver)
            return None
        logger.info(f"Scraped {len(text_content)} characters from {url}.")
        return text_content
    except TimeoutException:
        logger.error(f"Timeout loading {url}")
        save_failed_page(url, driver)
    except WebDriverException as e:
        logger.error(f"Selenium error for {url}: {e}")
        save_failed_page(url, driver)
    except Exception as ex:
        logger.error(f"Scraping {url} failed: {ex}")
        save_failed_page(url, driver)
    return None

def build_llm_prompt(
    text: str,
    instructions: str,
    standard_product_names: Optional[List[str]] = None
) -> str:
    """
    Build the final prompt to pass to the LLM, optionally including a list
    of standard product names or categories for cleaner output. 
    """
    base_prompt = f"""
Instructions:
{instructions}

Web Page Content:
{text}

Provide only the JSON output without any additional text or explanations.
Ensure that the JSON is a valid array of objects with the specified fields.
"""

    # Append standard product names if provided
    if standard_product_names:
        joined_names = "\n- " + "\n- ".join(standard_product_names)
        appended_text = f"""
When naming products, use the standardized product names below if any match or closely match the item:
{joined_names}

If no standard name fits, use a concise descriptive name that follows the same style.
"""
        base_prompt += appended_text.strip()

    return base_prompt

def process_with_llm(
    text: str,
    instructions: str,
    openai_api_key: str,
    retries: int = 2,
    standard_product_names: Optional[List[str]] = None
) -> Optional[str]:
    """Send scraped text to LLM and get response."""
    openai.api_key = openai_api_key

    # Build the final prompt
    prompt = build_llm_prompt(text, instructions, standard_product_names)

    for attempt in range(1, retries + 2):
        try:
            logger.info("LLM Parsing Initialized")
            response = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that strictly returns JSON data as per the instructions."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1500,
                n=1,
                stop=None,
                temperature=0.1,
            )
            llm_response = response.choices[0].message['content'].strip()
            logger.info("LLM response received.")
            return llm_response
        except Exception as e:
            logger.error(f"LLM Attempt {attempt} failed: {e}")
            if attempt <= retries:
                wait_time = random.uniform(1, 3)
                logger.info(f"Retrying in {wait_time:.2f}s")
                time.sleep(wait_time)
            else:
                logger.error("LLM max retries reached.")
                return None

def sanitize_llm_output(llm_output: str, url: str) -> Optional[List[dict]]:
    """Clean and parse LLM output into JSON."""
    try:
        # Remove code fences
        llm_output = re.sub(r'```json', '', llm_output, flags=re.IGNORECASE)
        llm_output = re.sub(r'```', '', llm_output)

        # Trim whitespace
        llm_output = llm_output.strip()

        # Parse JSON directly
        return json.loads(llm_output)
    except json.JSONDecodeError:
        logger.warning("Direct JSON parse failed. Attempting alternative extraction.")

        # Match JSON array or object
        json_match = re.search(r'(\[.*\])|(\{.*\})', llm_output, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse from extracted block failed: {e}")

        # Extract multiple JSON objects if necessary
        try:
            json_objects = re.findall(r'\{.*?\}', llm_output, re.DOTALL)
            if json_objects:
                return [json.loads(obj) for obj in json_objects]
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse from multiple objects failed: {e}")

        # Save raw output for debugging
        logger.error(f"JSON parsing failed for {url}")
        raw_output_file = os.path.join(OUTPUT_DIR, f"{sanitize_filename(url)}_llm_output.txt")
        with open(raw_output_file, 'w', encoding='utf-8') as f:
            f.write(llm_output)
        logger.info(f"Saved raw LLM output: {raw_output_file}")
        return None

def validate_json(data: List[dict], required_fields: Optional[set] = None) -> bool:
    """Ensure JSON data has required structure/fields"""
    if required_fields is None:
        return True

    if not isinstance(data, list):
        logger.error("LLM output is not a list. Validation failed.")
        return False

    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            logger.error(f"Item {idx} is not a dict. Validation failed.")
            return False
        if not required_fields.issubset(item.keys()):
            missing = required_fields - item.keys()
            logger.error(f"Item {idx} missing required fields: {missing}. Validation failed.")
            return False

    return True

def scrape_and_process_service(
    urls: List[str],
    instructions: str,
    openai_api_key: str,
    headless: bool = True,
    standard_product_names: Optional[List[str]] = None,
    required_fields: Optional[List[str]] = None
) -> Optional[str]:
    """
    Main function to scrape URLs and process data:
      - Scrapes each URL with Selenium
      - Sends the text and user instructions to the LLM
      - Validates JSON
      - Saves all results into a single CSV file
    :param urls: URLs to scrape
    :param instructions: Prompt or instructions for the LLM
    :param openai_api_key: Your OpenAI API key
    :param headless: Use headless browser or not
    :param standard_product_names: Optional list of product names/categories to standardize
    :param required_fields: List of fields each JSON object must contain
    :return: Path to the combined CSV file (if success), otherwise None
    """
    driver = setup_driver(headless=headless)
    if not driver:
        logger.error("Driver initialization failed. Exiting.")
        return None

    all_data = []
    try:
        for url in urls:
            logger.info(f"Scraping {url}")
            text = scrape_with_selenium(url, driver)
            if not text:
                continue

            llm_output = process_with_llm(
                text=text,
                instructions=instructions,
                openai_api_key=openai_api_key,
                standard_product_names=standard_product_names
            )
            if not llm_output:
                continue

            data = sanitize_llm_output(llm_output, url)
            if not data:
                continue

            if required_fields:
                if not validate_json(data, set(required_fields)):
                    logger.error(f"Validation failed for data from {url}. Skipping.")
                    continue

            # Accumulate data
            all_data.extend(data)
            logger.info(f"Processed {url}")
            time.sleep(random.uniform(*DELAY_RANGE))
    except Exception as e:
        logger.error(f"Scraping process failed: {e}")
    finally:
        driver.quit()
        logger.info("Driver closed.")

    # Save data to CSV if any
    if all_data:
        timestamp = int(time.time())
        output_file = os.path.join(OUTPUT_DIR, f"results_{timestamp}.csv")
        try:
            df = pd.DataFrame(all_data)
            df.to_csv(output_file, index=False, encoding='utf-8')
            logger.info(f"All data saved to {output_file}.")
            return output_file
        except Exception as ex:
            logger.error(f"Saving to CSV failed: {ex}")
    else:
        logger.error("No data extracted.")
    return None
