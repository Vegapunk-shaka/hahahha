import os
import time
import json
import logging
import argparse
import subprocess
import requests
import re
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

class UdvashDownloader:
    def __init__(self, user_id, password, max_parallel_downloads=3, download_dir="downloads", 
                 download_archive=True, download_marathon=True, download_bangla=True,
                 download_english=True, create_json=True):
        # Setup logging
        self.setup_logger()
        
        # Login credentials
        self.user_id = user_id
        self.password = password
        
        # Download settings
        self.download_dir = download_dir
        self.max_parallel_downloads = max_parallel_downloads
        self.active_downloads = 0
        self.download_queue = []
        
        # Content options
        self.download_archive = download_archive
        self.download_marathon = download_marathon
        self.download_bangla = download_bangla
        self.download_english = download_english
        self.create_json = create_json
        
        # Topic structure for JSON output
        self.topic_structure = {}
        
        # Create download directory
        os.makedirs(download_dir, exist_ok=True)
        
        # Configure Chrome webdriver
        self.setup_webdriver()
        
        # Wait conditions
        self.wait = WebDriverWait(self.driver, 20)
        self.short_wait = WebDriverWait(self.driver, 5)
        
        # Login to the website
        if not self.login():
            self.logger.error("Login failed! Exiting...")
            self.cleanup()
            exit(1)
    
    def setup_logger(self):
        """Set up logging configuration"""
        self.logger = logging.getLogger("udvash_downloader")
        self.logger.setLevel(logging.INFO)
        
        # Create formatter
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        
        # Create console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # Create file handler
        file_handler = logging.FileHandler("udvash_downloader.log")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
    
    def setup_webdriver(self):
        """Configure and initialize Chrome webdriver"""
        chrome_options = Options()
        chrome_options.add_argument("--headless")  # Run in headless mode
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=640,360")
        chrome_options.add_argument("--log-level=3")  # Suppress logging
        
        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.set_page_load_timeout(60)
        self.logger.info("WebDriver initialized successfully")
    
    def login(self):
        """Handle login process"""
        self.logger.info("Attempting login...")
        try:
            self.driver.get("https://online.udvash-unmesh.com/Account/Login")
            time.sleep(1)
            
            # Enter registration number
            reg_input = self.wait.until(EC.presence_of_element_located((By.ID, "RegistrationNumber")))
            reg_input.send_keys(self.user_id)
            
            # Click continue
            continue_btn = self.wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit")))
            continue_btn.click()
            time.sleep(1)
            
            # Enter password
            pass_input = self.wait.until(EC.presence_of_element_located((By.ID, "Password")))
            pass_input.send_keys(self.password)
            
            # Click login
            login_btn = self.wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "uu-button-style-2")))
            login_btn.click()
            
            # Wait for dashboard
            self.wait.until(EC.url_contains("Dashboard"))
            self.logger.info("Login successful!")
            return True
        except Exception as e:
            self.logger.error(f"Login failed: {str(e)}")
            return False
    
    def wait_for_elements(self, css_selector, timeout=20):
        """Wait for elements to be present and return them"""
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
            )
            return self.driver.find_elements(By.CSS_SELECTOR, css_selector)
        except TimeoutException:
            self.logger.warning(f"Timeout waiting for elements: {css_selector}")
            return []
    
    def get_subjects(self, course_type_id=2, master_course_id=11):
        """Get all subject links and names"""
        self.logger.info("Getting subjects...")
        self.driver.get(f"https://online.udvash-unmesh.com/Content/ContentSubject?CourseTypeId={course_type_id}&masterCourseId={master_course_id}&ln=En")
        
        subjects = []
        subject_elements = self.wait_for_elements("div.col-xl-4.col-lg-6.d-flex a")
        
        for idx, element in enumerate(subject_elements, 1):
            try:
                href = element.get_attribute("href")
                name_elem = element.find_element(By.CSS_SELECTOR, "h3")
                name = name_elem.text.strip()
                
                # Extract subject ID from href
                url_parts = urlparse(href)
                query_params = parse_qs(url_parts.query)
                subject_id = query_params.get('subjectId', [''])[0]
                
                # Use a single letter as subject prefix based on index
                subject_prefix = chr(64 + idx)  # A = 65 in ASCII, so 1 -> A, 2 -> B, etc.
                
                subjects.append({
                    'index': idx,
                    'name': name,
                    'url': href,
                    'id': subject_id,
                    'prefix': subject_prefix
                })
                self.logger.info(f"Found subject {idx}: {name} (ID: {subject_id}, Prefix: {subject_prefix})")
            except Exception as e:
                self.logger.error(f"Error processing subject element: {str(e)}")
        
        return subjects
    
    def get_chapters(self, subject_url, subject_name, subject_prefix):
        """Get all chapter links and names for a subject"""
        self.logger.info(f"Getting chapters for subject: {subject_name}")
        try:
            self.driver.get(subject_url)
            
            chapters = []
            chapter_elements = self.wait_for_elements("div.col-xl-4.col-lg-6.d-flex a")
            
            for idx, element in enumerate(chapter_elements, 1):
                try:
                    href = element.get_attribute("href")
                    name_elem = element.find_element(By.CSS_SELECTOR, "h3")
                    chapter_name = name_elem.text.strip()
                    
                    # Extract chapter ID from href
                    url_parts = urlparse(href)
                    query_params = parse_qs(url_parts.query)
                    chapter_id = query_params.get('masterChapterId', [''])[0]
                    
                    # Use consistent index format: SubjectPrefix.ChapterNumber (e.g., A.1, B.3)
                    formatted_index = f"{subject_prefix}.{idx}"
                    
                    chapters.append({
                        'index': formatted_index,
                        'name': chapter_name,
                        'url': href,
                        'id': chapter_id,
                        'subject_name': subject_name,
                        'subject_prefix': subject_prefix,
                        'chapter_number': idx  # Store the chapter number separately for easy filtering
                    })
                    self.logger.info(f"Found chapter {formatted_index}: {chapter_name} (ID: {chapter_id})")
                except Exception as e:
                    self.logger.error(f"Error processing chapter element: {str(e)}")
            
            return chapters
        except Exception as e:
            self.logger.error(f"Error getting chapters: {str(e)}")
            return []
    
    def get_content_types(self, chapter_url, chapter_name):
        """Get content types (marathon, archive, etc.) for a chapter"""
        self.logger.info(f"Getting content types for chapter: {chapter_name}")
        try:
            self.driver.get(chapter_url)
            time.sleep(2)
            
            # Extract parameters from the current URL
            url_parts = urlparse(self.driver.current_url)
            query_params = parse_qs(url_parts.query)
            master_course_id = query_params.get('masterCourseId', [''])[0]
            subject_id = query_params.get('subjectId', [''])[0]
            master_chapter_id = query_params.get('masterChapterId', [''])[0]
            
            content_types = []
            
            # Marathon content (masterContentTypeId=2)
            if self.download_marathon:
                marathon_url = f"https://online.udvash-unmesh.com/Content/DisplayContentCard?masterCourseId={master_course_id}" + \
                              f"&subjectId={subject_id}&masterChapterId={master_chapter_id}&masterContentTypeId=2"
                content_types.append({
                    'name': 'Marathon',
                    'url': marathon_url,
                    'type_id': '2'
                })
            
            # Archive content (masterContentTypeId=9)
            if self.download_archive:
                archive_url = f"https://online.udvash-unmesh.com/Content/DisplayContentCard?masterCourseId={master_course_id}" + \
                             f"&subjectId={subject_id}&masterChapterId={master_chapter_id}&masterContentTypeId=9"
                content_types.append({
                    'name': 'Archive',
                    'url': archive_url,
                    'type_id': '9'
                })
            
            return content_types, master_course_id, subject_id, master_chapter_id
        except Exception as e:
            self.logger.error(f"Error getting content types: {str(e)}")
            return [], '', '', ''
    
    def get_topic_from_content_card(self, card_element):
        """Extract topic name from content card HTML"""
        try:
            # Find the content div
            content_div = card_element.find_element(By.CSS_SELECTOR, "div.content")
            if not content_div:
                return None
                
            # Get the HTML content
            content_html = content_div.get_attribute('innerHTML')
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(content_html, 'html.parser')
            
            # Try to find a strong tag with topic information (look for the second one, which is typically the topic)
            # First, look for all strong tags or spans with strong inside
            strong_elements = soup.find_all('strong')
            if len(strong_elements) >= 2:
                # The second strong element often contains the topic name
                topic_text = strong_elements[1].get_text(strip=True)
                self.logger.info(f"Extracted topic from strong element: {topic_text}")
                return topic_text
                
            # If we couldn't find the topic in strong tags, try looking for it in the table cells
            # Get all table cells
            all_cells = soup.find_all('td')
            
            # Look for the cell that contains the "◾" character or has topic-like content
            for cell in all_cells:
                cell_text = cell.get_text(strip=True)
                if "◾" in cell_text or (len(cell_text) > 5 and not cell_text.startswith("🔸")):
                    # Process to clean up the text
                    topic_text = re.sub(r'^\s*◾\s*', '', cell_text)
                    self.logger.info(f"Extracted topic from cell: {topic_text}")
                    return topic_text
            
            # If we still can't find a topic, look for the last non-empty cell as a fallback
            non_empty_cells = [cell for cell in all_cells if cell.get_text(strip=True)]
            if non_empty_cells:
                topic_text = non_empty_cells[-1].get_text(strip=True)
                topic_text = re.sub(r'^\s*◾\s*', '', topic_text)
                self.logger.info(f"Extracted topic from last non-empty cell: {topic_text}")
                return topic_text
                
            # If all else fails, return a default topic
            return "General"
        except Exception as e:
            self.logger.error(f"Error extracting topic name: {str(e)}")
            return "Unknown Topic"  # Return a default value instead of None
    
    def get_content_cards(self, content_type_url, content_type_name):
        """Get content cards from a content type page"""
        self.logger.info(f"Getting content cards for {content_type_name}...")
        try:
            self.driver.get(content_type_url)
            time.sleep(2)
            
            cards = []
            card_elements = self.wait_for_elements("div.col-xl-3.col-lg-4.col-md-6.d-flex .card")
            
            for idx, card in enumerate(card_elements, 1):
                try:
                    # Get card title
                    title_elem = card.find_element(By.CSS_SELECTOR, "h2.uuu-wrap-title")
                    title = title_elem.text.strip()
                    
                    # Get video and note links
                    video_link = card.find_element(By.CSS_SELECTOR, "a.btn-video").get_attribute("href")
                    note_link = card.find_element(By.CSS_SELECTOR, "a.btn-note").get_attribute("href")
                    
                    # Extract content ID from the link
                    url_parts = urlparse(video_link)
                    query_params = parse_qs(url_parts.query)
                    content_id = query_params.get('masterContentId', [''])[0]
                    
                    # Extract topic information
                    topic = self.get_topic_from_content_card(card)
                    
                    cards.append({
                        'index': idx,
                        'title': title,
                        'video_link': video_link,
                        'note_link': note_link,
                        'content_id': content_id,
                        'topic': topic
                    })
                    self.logger.info(f"Found content card {idx}: {title} (ID: {content_id}, Topic: {topic})")
                except NoSuchElementException:
                    self.logger.warning(f"Skipping a card that doesn't have all required elements")
                except Exception as e:
                    self.logger.error(f"Error processing content card: {str(e)}")
            
            return cards
        except Exception as e:
            self.logger.error(f"Error getting content cards: {str(e)}")
            return []
    
    def extract_video_url(self, video_page_url):
        """Extract video download URL from video page"""
        self.logger.info(f"Extracting video URL from: {video_page_url}")
        try:
            self.driver.get(video_page_url)
            time.sleep(2)
            
            # Get page source and find video source
            page_source = self.driver.page_source
            video_src_match = re.search(r'<source src="([^"]+)" type="video/mp4">', page_source)
            
            if video_src_match:
                # Get the raw URL and decode HTML entities like &
                raw_video_url = video_src_match.group(1)
                
                # Replace HTML entities with proper characters
                video_url = raw_video_url.replace("&", "&")
                
                self.logger.info(f"Found video URL: {video_url[:100]}...")
                return video_url
            else:
                self.logger.warning("No video source found in the page")
                return None
        except Exception as e:
            self.logger.error(f"Error extracting video URL: {str(e)}")
            return None
    
    def extract_pdf_url(self, pdf_page_url):
        """Extract PDF download URL from PDF/note page"""
        self.logger.info(f"Extracting PDF URL from: {pdf_page_url}")
        try:
            self.driver.get(pdf_page_url)
            time.sleep(2)
            
            # Look for download button with href
            pdf_link_elem = self.driver.find_element(By.CSS_SELECTOR, "a.btn-success[href]")
            raw_pdf_url = pdf_link_elem.get_attribute("href")
            
            # Replace HTML entities (just in case)
            pdf_url = raw_pdf_url.replace("&", "&") if raw_pdf_url else None
            
            if pdf_url:
                self.logger.info(f"Found PDF URL: {pdf_url[:100]}...")
                return pdf_url
            else:
                self.logger.warning("No PDF download link found")
                return None
        except NoSuchElementException:
            self.logger.warning("PDF download button not found on the page")
            return None
        except Exception as e:
            self.logger.error(f"Error extracting PDF URL: {str(e)}")
            return None
    
    def download_file(self, url, file_path, file_type):
        """Download a file using aria2c or yt-dlp based on file type"""
        self.logger.info(f"Downloading {file_type} from {url}")
        
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            if file_type == "video":
                # First try aria2c
                try:
                    subprocess.run(["aria2c", "-j", "64", "-o", file_path, url], check=True)
                    self.logger.info(f"Downloaded video using aria2c: {file_path}")
                    return True
                except Exception as e:
                    self.logger.warning(f"aria2c failed, trying yt-dlp: {str(e)}")
                    
                # If aria2c fails, try yt-dlp
                try:
                    subprocess.run(["yt-dlp", "-N", "64", "-o", file_path, url], check=True)
                    self.logger.info(f"Downloaded video using yt-dlp: {file_path}")
                    return True
                except Exception as e:
                    self.logger.error(f"Both download methods failed: {str(e)}")
                    return False
            else:  # PDF
                try:
                    subprocess.run(["aria2c", "-j", "64", "-o", file_path, url], check=True)
                    self.logger.info(f"Downloaded PDF: {file_path}")
                    return True
                except Exception as e:
                    self.logger.error(f"PDF download failed: {str(e)}")
                    return False
        except Exception as e:
            self.logger.error(f"Error in download_file: {str(e)}")
            return False
        finally:
            # Decrease active downloads count
            self.active_downloads -= 1
            # Process any queued downloads
            self.process_download_queue()
    
    def queue_download(self, url, file_path, file_type):
        """Queue a download or start it if under the limit"""
        if self.active_downloads < self.max_parallel_downloads:
            self.active_downloads += 1
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(self.download_file, url, file_path, file_type)
        else:
            self.download_queue.append((url, file_path, file_type))
            self.logger.info(f"Queued {file_type}多元download: {os.path.basename(file_path)}")
    
    def process_download_queue(self):
        """Process any queued downloads if under the limit"""
        if self.download_queue and self.active_downloads < self.max_parallel_downloads:
            url, file_path, file_type = self.download_queue.pop(0)
            self.active_downloads += 1
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(self.download_file, url, file_path, file_type)
    
    def add_to_topic_structure(self, subject_name, chapter_name, content_type_name, topic_name, card_title):
        """Add a topic to the topic structure for JSON output"""
        if self.create_json:
            # Initialize subject if not exists
            if subject_name not in self.topic_structure:
                self.topic_structure[subject_name] = {}
            
            # Initialize chapter if not exists
            if chapter_name not in self.topic_structure[subject_name]:
                self.topic_structure[subject_name][chapter_name] = {}
            
            # Initialize content type if not exists
            if content_type_name not in self.topic_structure[subject_name][chapter_name]:
                self.topic_structure[subject_name][chapter_name][content_type_name] = {}
            
            # Initialize topic if not exists
            if topic_name not in self.topic_structure[subject_name][chapter_name][content_type_name]:
                self.topic_structure[subject_name][chapter_name][content_type_name][topic_name] = []
            
            # Add card title to topic
            self.topic_structure[subject_name][chapter_name][content_type_name][topic_name].append(card_title)
    
    def process_content(self, subject_name, chapter_name, content_card, master_course_id, subject_id, master_chapter_id, content_type_name):
        """Process a content card for both video and PDF download"""
        title = content_card['title']
        topic = content_card['topic']
        clean_title = re.sub(r'[<>:"/\\|?*]', '_', title)  # Remove invalid filename chars
        
        # Create directory for the download
        base_dir = os.path.join(self.download_dir, subject_name, chapter_name, content_type_name)
        os.makedirs(base_dir, exist_ok=True)
        
        # Add to topic structure
        self.add_to_topic_structure(subject_name, chapter_name, content_type_name, topic, title)
        
        # Process Bangla version
        if self.download_bangla:
            try:
                self.logger.info(f"Processing Bangla content for: {title}")
                
                # Process video
                video_url = content_card['video_link'].replace("ln=En", "ln=Bn") if "ln=En" in content_card['video_link'] else content_card['video_link']
                video_download_url = self.extract_video_url(video_url)
                
                if video_download_url:
                    video_filename = f"{clean_title}_Bn.mp4"
                    video_path = os.path.join(base_dir, video_filename)
                    
                    # Check if file already exists
                    if os.path.exists(video_path):
                        self.logger.info(f"Video already exists, skipping: {video_filename}")
                    else:
                        self.queue_download(video_download_url, video_path, "video")
                else:
                    self.logger.warning(f"No Bangla video URL found for {title}")
                
                # Process PDF/note
                pdf_url = content_card['note_link'].replace("ln=En", "ln=Bn") if "ln=En" in content_card['note_link'] else content_card['note_link']
                pdf_download_url = self.extract_pdf_url(pdf_url)
                
                if pdf_download_url:
                    pdf_filename = f"{clean_title}_Bn.pdf"
                    pdf_path = os.path.join(base_dir, pdf_filename)
                    
                    # Check if file already exists
                    if os.path.exists(pdf_path):
                        self.logger.info(f"PDF already exists, skipping: {pdf_filename}")
                    else:
                        self.queue_download(pdf_download_url, pdf_path, "pdf")
                else:
                    self.logger.warning(f"No Bangla PDF URL found for {title}")
            except Exception as e:
                self.logger.error(f"Error processing Bangla content: {str(e)}")
        
        # Process English version
        if self.download_english:
            try:
                self.logger.info(f"Processing English content for: {title}")
                
                # Process video
                video_url = content_card['video_link'].replace("ln=Bn", "ln=En") if "ln=Bn" in content_card['video_link'] else content_card['video_link']
                video_download_url = self.extract_video_url(video_url)
                
                if video_download_url:
                    video_filename = f"{clean_title}_En.mp4"
                    video_path = os.path.join(base_dir, video_filename)
                    
                    # Check if file already exists
                    if os.path.exists(video_path):
                        self.logger.info(f"Video already exists, skipping: {video_filename}")
                    else:
                        self.queue_download(video_download_url, video_path, "video")
                else:
                    self.logger.warning(f"No English video URL found for {title}")
                
                # Process PDF/note
                pdf_url = content_card['note_link'].replace("ln=Bn", "ln=En") if "ln=Bn" in content_card['note_link'] else content_card['note_link']
                pdf_download_url = self.extract_pdf_url(pdf_url)
                
                if pdf_download_url:
                    pdf_filename = f"{clean_title}_En.pdf"
                    pdf_path = os.path.join(base_dir, pdf_filename)
                    
                    # Check if file already exists
                    if os.path.exists(pdf_path):
                        self.logger.info(f"PDF already exists, skipping: {pdf_filename}")
                    else:
                        self.queue_download(pdf_download_url, pdf_path, "pdf")
                else:
                    self.logger.warning(f"No English PDF URL found for {title}")
            except Exception as e:
                self.logger.error(f"Error processing English content: {str(e)}")
    
    def save_topic_structure(self):
        """Save the topic structure to a JSON file"""
        if self.create_json:
            json_path = os.path.join(self.download_dir, "topic_structure.json")
            self.logger.info(f"Saving topic structure to: {json_path}")
            
            try:
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(self.topic_structure, f, indent=2, ensure_ascii=False)
                self.logger.info("Topic structure saved successfully")
            except Exception as e:
                self.logger.error(f"Error saving topic structure: {str(e)}")
    
    def wait_for_downloads_to_complete(self):
        """Wait for all downloads to complete"""
        self.logger.info("Waiting for all downloads to complete...")
        while self.active_downloads > 0 or self.download_queue:
            time.sleep(1)
        self.logger.info("All downloads completed!")
    
    def process_chapter(self, chapter):
        """Process a single chapter"""
        self.logger.info(f"Processing chapter: {chapter['index']} {chapter['name']}")
        subject_name = chapter['subject_name']
        
        try:
            # Get content types (marathon, archive)
            content_types, master_course_id, subject_id, master_chapter_id = self.get_content_types(chapter['url'], chapter['name'])
            
            if not content_types:
                self.logger.warning(f"No content types found for chapter: {chapter['name']}")
                return
            
            # Process each content type
            for content_type in content_types:
                self.logger.info(f"Processing content type: {content_type['name']} for chapter: {chapter['name']}")
                
                # Get content cards for this type
                content_cards = self.get_content_cards(content_type['url'], content_type['name'])
                
                if not content_cards:
                    self.logger.warning(f"No content cards found for {content_type['name']}")
                    continue
                
                # Process each content card
                for card in content_cards:
                    self.process_content(
                        subject_name,
                        chapter['name'], 
                        card, 
                        master_course_id, 
                        subject_id, 
                        master_chapter_id, 
                        content_type['name']
                    )
            
            # Save topic structure after processing each chapter
            self.save_topic_structure()
            
        except Exception as e:
            self.logger.error(f"Error processing chapter {chapter['name']}: {str(e)}")
    
    def download_all(self, from_chapter=None, to_chapter=None, specific_subjects=None):
        """Download all content or specific chapter range"""
        try:
            # Get all subjects
            subjects = self.get_subjects()
            
            if not subjects:
                self.logger.error("No subjects found!")
                return
            
            # Filter subjects if specific ones are requested
            if specific_subjects:
                subjects = [s for s in subjects if s['name'] in specific_subjects]
                self.logger.info(f"Filtered to {len(subjects)} specific subjects")
            
            all_chapters = []
            
            # Get chapters for each subject
            for subject in subjects:
                chapters = self.get_chapters(subject['url'], subject['name'], subject['prefix'])
                for chapter in chapters:
                    all_chapters.append(chapter)
            
            # Filter chapters based on from_chapter and to_chapter parameters
            chapters_to_process = []
            
            if from_chapter is not None or to_chapter is not None:
                # Parse from_chapter and to_chapter
                from_subject_prefix = None
                from_chapter_number = None
                to_subject_prefix = None
                to_chapter_number = None
                
                if from_chapter is not None:
                    # Check if it's a composite format like "B.3" or just a number
                    if isinstance(from_chapter, str) and '.' in from_chapter:
                        parts = from_chapter.split('.')
                        from_subject_prefix = parts[0].upper()
                        from_chapter_number = int(parts[1]) if len(parts) > 1 else 1
                    else:
                        # If it's just a number, interpret based on subject order
                        from_chapter = int(from_chapter)
                        for s in subjects:
                            chapters = self.get_chapters(s['url'], s['name'], s['prefix'])
                            if from_chapter <= len(chapters):
                                from_subject_prefix = s['prefix']
                                from_chapter_number = from_chapter
                                break
                            from_chapter -= len(chapters)
                
                if to_chapter is not None:
                    # Check if it's a composite format like "B.3" or just a number
                    if isinstance(to_chapter, str) and '.' in to_chapter:
                        parts = to_chapter.split('.')
                        to_subject_prefix = parts[0].upper()
                        to_chapter_number = int(parts[1]) if len(parts) > 1 else 1
                    else:
                        # If it's just a number, interpret based on subject order
                        to_chapter = int(to_chapter)
                        found = False
                        for s in subjects:
                            chapters = self.get_chapters(s['url'], s['name'], s['prefix'])
                            if to_chapter <= len(chapters):
                                to_subject_prefix = s['prefix']
                                to_chapter_number = to_chapter
                                found = True
                                break
                            to_chapter -= len(chapters)
                            
                        if not found:
                            # If we couldn't find the chapter, use the last chapter of the last subject
                            to_subject_prefix = subjects[-1]['prefix']
                            to_chapter_numberIt = len(self.get_chapters(subjects[-1]['url'], subjects[-1]['name'], subjects[-1]['prefix']))
                
                # Use the calculated values to filter chapters
                started = from_subject_prefix is None
                for chapter in all_chapters:
                    subject_prefix = chapter['subject_prefix']
                    chapter_number = chapter['chapter_number']
                    
                    # If we haven't started and this is our start point
                    if not started and subject_prefix == from_subject_prefix and chapter_number >= from_chapter_number:
                        started = True
                    
                    # Add chapter if we've started
                    if started:
                        chapters_to_process.append(chapter)
                    
                    # Stop if we've reached our end point
                    if to_subject_prefix is not None and subject_prefix == to_subject_prefix and chapter_number >= to_chapter_number:
                        break
            else:
                chapters_to_process = all_chapters
            
            self.logger.info(f"Processing {len(chapters_to_process)} chapters")
            
            # Debug information
            for ch in chapters_to_process:
                self.logger.info(f"Will process: {ch['index']} - {ch['name']}")
            
            # Process each chapter in a new thread to enable concurrent processing
            with ThreadPoolExecutor(max_workers=1) as executor:
                for chapter in chapters_to_process:
                    executor.submit(self.process_chapter, chapter)
                    # Small delay to allow the download queue to start processing
                    time.sleep(0.5)
            
            # Wait for all downloads to complete
            self.wait_for_downloads_to_complete()
            
            # Save final topic structure
            self.save_topic_structure()
            
        except Exception as e:
            self.logger.error(f"Error in download_all: {str(e)}")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Clean up resources"""
        self.logger.info("Cleaning up resources...")
        try:
            self.driver.quit()
        except:
            pass
