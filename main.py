from flask import Flask, request, jsonify
import os
import requests
import zipfile
import subprocess
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

app = Flask(__name__)

# URLs for the Chrome Headless Shell and ChromeDriver
chrome_headless_url = "https://storage.googleapis.com/chrome-for-testing-public/126.0.6478.182/linux64/chrome-headless-shell-linux64.zip"
chrome_driver_url = "https://storage.googleapis.com/chrome-for-testing-public/126.0.6478.182/linux64/chromedriver-linux64.zip"

# Paths to save the downloaded files
chrome_headless_zip_path = "chrome-headless-shell-linux64.zip"
chrome_driver_zip_path = "chromedriver-linux64.zip"

# Paths for extracted files
chrome_headless_path = "chrome-headless-shell-linux64/chrome-headless-shell-linux64/chrome-headless-shell"
chrome_driver_path = "chromedriver-linux64/chromedriver-linux64/chromedriver"

def download_file(url, local_path):
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(local_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)
    print(f"Downloaded {url} to {local_path}")

def unzip_file(zip_path, extract_to):
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)
    print(f"Extracted {zip_path} to {extract_to}")

# Check if Chrome Headless Shell and ChromeDriver are already available
if not os.path.isfile(chrome_headless_path) or not os.path.isfile(chrome_driver_path):
    # Download Chrome Headless Shell and ChromeDriver if not already present
    if not os.path.isfile(chrome_headless_zip_path):
        download_file(chrome_headless_url, chrome_headless_zip_path)
    if not os.path.isfile(chrome_driver_zip_path):
        download_file(chrome_driver_url, chrome_driver_zip_path)

    # Unzip the downloaded files
    if not os.path.isfile(chrome_headless_path):
        unzip_file(chrome_headless_zip_path, "chrome-headless-shell-linux64")
    if not os.path.isfile(chrome_driver_path):
        unzip_file(chrome_driver_zip_path, "chromedriver-linux64")

    # Set executable permissions
    subprocess.run(["chmod", "+x", chrome_headless_path])
    subprocess.run(["chmod", "+x", chrome_driver_path])
else:
    print("Chrome Headless Shell and ChromeDriver are already available.")

# Set Chrome options
chrome_options = Options()
chrome_options.binary_location = chrome_headless_path
chrome_options.add_argument('--headless')
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument('--disable-gpu')

# Initialize Chrome webdriver
service = Service(chrome_driver_path)
driver = webdriver.Chrome(service=service, options=chrome_options)

@app.route('/get_page_source', methods=['GET'])
def get_page_source():
    url = request.args.get('url', 'https://epaper.eenadu.net/Home/Index?date=04/08/2024&eid=1')
    driver.get(url)

    try:
        # Wait for 10 seconds to ensure the page loads completely
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # Get the page source (HTML content)
        page_source = driver.page_source

        return jsonify({'page_source': page_source})

    except TimeoutException:
        return jsonify({'error': 'Timeout occurred while waiting for the page to load.'}), 500
    finally:
        # Close the browser
        driver.quit()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3080)  # Render expects the app to be available on port 8080
