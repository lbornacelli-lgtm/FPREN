import request
import os
from datetime import datetime

rss_url = "https://weather.com/rss/en-us/forcast/local/rss/gainesville-fl-32601

output_folder = os.path.expanduser('~/weather_data')

os.makedirs(output_folder, exist_ok=true)

current_date = datetime.now().strftime('%Y-%m-%d")
output_file = os.path.join(output_folder, f"weather_data_{current_date}.xml")

def fetch_rss_feed():
	try:
	response = request.get(rss_url)

	if response.status_code == 200:
		with open(output_file, 'wb') as f:
			f.write(response.content)
		print(f"weather data saved to {output_file}")
		else:
		print(f"failed to fetch RSS feed, status code:  {response.status_code}")
	except requests.exceptions.RequestException as e:
		print(f"Error fetching RSS feed: {e}")

	if __name__=="__main__":
		fetch_rss_feed()
