import abc
from bs4 import BeautifulSoup
import csv
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
# import from webdriver_manager (using underscore)
from webdriver_manager.chrome import ChromeDriverManager
from time import sleep
import random
import logging


class EmptyRowData(Exception):
    ...


class Product(abc.ABC):
    def __init__(self, product_name, base_price, config_price, bundle_price, product_code, max_pgs=None, start_pgs=None):
        self.GENERAL = "general"
        self.CRITICAL = "critical"
        self.POSITIVE = "positive"

        self.product_name = product_name
        self.base_price = base_price
        self.config_price = config_price
        self.bundle_price = bundle_price
        self.product_code = product_code
        self.set_max_pgs(max_pgs)

        self.pgs_lower_bound = {
            self.GENERAL: 2,
            self.CRITICAL: 1,
            self.POSITIVE: 1,
        }

        self.start_pgs = start_pgs
        if self.start_pgs:
            self.start_pgs[self.GENERAL] = min(
                500, max(self.pgs_lower_bound[self.GENERAL], self.start_pgs[self.GENERAL]))
            self.start_pgs[self.CRITICAL] = min(
                500, max(1, self.start_pgs[self.CRITICAL]))
            self.start_pgs[self.POSITIVE] = min(
                500, max(1, self.start_pgs[self.POSITIVE]))
        else:
            self.start_pgs = {
                self.GENERAL: self.pgs_lower_bound[self.GENERAL],
                self.CRITICAL: self.pgs_lower_bound[self.CRITICAL],
                self.POSITIVE: self.pgs_lower_bound[self.POSITIVE],
            }

    def set_max_pgs(self, max_pgs):
        if max_pgs:
            # amazon only shows max of 500 pages
            self.max_pgs = {k: min(500, v) for k, v in max_pgs.items()}
        else:
            self.max_pgs = {
                self.GENERAL: 500,
                self.CRITICAL: 500,
                self.POSITIVE: 500,
            }

    def get_pages(self):
        return {
            self.CRITICAL: self.critical(),
            self.POSITIVE: self.positive(),
            self.GENERAL: self.general(),
        }

    def general(self):
        first = ("https://www.amazon.com/product-reviews"
                 f"/{self.product_code}"
                 "/ref=cm_cr_dp_d_show_all_btm"
                 "?ie=UTF8&reviewerType=all_reviews")

        pg_tmplate = ("https://www.amazon.com/product-reviews"
                      f"/{self.product_code}"
                      "/ref=cm_cr_arp_d_paging_btm_next_{}"
                      "?ie=UTF8&reviewerType=all_reviews"
                      "&pageNumber={}")

        start, end = self.start_pgs[self.GENERAL], self.max_pgs["general"] + 1
        return [first] + [pg_tmplate.format(i, i) for i in range(start, end)]

    def positive(self):
        pg_tmplate = ("https://www.amazon.com/product-reviews"
                      f"/{self.product_code}"
                      "/ref=cm_cr_arp_d_viewopt_sr"
                      "?ie=UTF8&reviewerType=all_reviews"
                      "&filterByStar=positive&pageNumber={}")

        start, end = self.start_pgs[self.POSITIVE], self.max_pgs["positive"] + 1
        return [pg_tmplate.format(i) for i in range(start, end)]

    def critical(self):
        pg_tmplate = ("https://www.amazon.com/product-reviews"
                      f"/{self.product_code}"
                      "/ref=cm_cr_arp_d_viewopt_sr"
                      "?ie=UTF8&reviewerType=all_reviews"
                      "&filterByStar=critical&pageNumber={}")

        start, end = self.start_pgs[self.CRITICAL], self.max_pgs[self.CRITICAL] + 1
        return [pg_tmplate.format(i) for i in range(start, end)]


class Scraper:
    def __init__(self, sleep_mean, sleep_sigma, products: "list[Product]", global_max_pgs=None):
        self.products = products
        self.sleep_mean = sleep_mean
        self.sleep_sigma = sleep_sigma
        self.global_max_pgs = global_max_pgs

        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        self.driver = webdriver.Chrome(options=options, service=Service(
            ChromeDriverManager().install()))
        # self.driver.delete_all_cookies()

    def scrape(self):
        for product in self.products:
            product.set_max_pgs(
                self.global_max_pgs) if self.global_max_pgs else None
            for name, pages in product.get_pages().items():
                try:
                    self._scrape_product_with_review_type(
                        product, pages, name)
                except EmptyRowData:
                    logging.exception(
                        "Found empty row, moving to nex set of pages")

    def _scrape_product_with_review_type(self, product, pages, orig_type):
        col_data = {
            "product_name": product.product_name,
            "base_price": product.base_price,
            "config_price": product.config_price,
            "bundle_price": product.bundle_price,
            "names": [],
            "stars": [],
            "dates": [],
            "titles": [],
            "reviews": [],
            "helpfuls": [],
            "config_color": []
        }
        print(product.start_pgs[orig_type])
        if product.start_pgs[orig_type] < 3:
            product.start_pgs[orig_type] = product.pgs_lower_bound[orig_type]

            self.write_col_names_to_csv_file(
                orig_type, product.product_name, col_data.keys())

        list_of_urls = pages
        logging.info("extracting from %s to %s",
                     list_of_urls[0], list_of_urls[-1])
        for idx, url in enumerate(list_of_urls):
            print(idx, orig_type, product.product_name, url)
            logging.info("%d %s %s %s", idx, orig_type,
                         product.product_name, url)
            self.driver.get(url)
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.ID, "cm_cr-review_list"))
            )
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            # reset col_data values
            for k, v in col_data.items():
                col_data[k] = [] if isinstance(
                    col_data[k], list) else col_data[k]

            for review_items in soup.find_all("div", id="cm_cr-review_list"):
                self._scrape_page(product, orig_type,
                                   soup, col_data, review_items)

                sleep_for = max(random.gauss(
                    self.sleep_mean, self.sleep_sigma), 1)
                print("sleeping for", sleep_for)
                # logging.info("sleeping for %s", sleep_for)
                sleep(sleep_for)

    def write_col_names_to_csv_file(self, orig_type, product_name, col_names):
        with open(f"scrape_data_cwd/{orig_type}_{product_name}_{CURR_DT_TIME}.csv", "w", encoding="utf-8") as f:
            writer = csv.writer(f, lineterminator='\n')
            writer.writerow(col_names)

    def _scrape_page(self, product, orig_type, soup, col_data, review_items):
        self._extract_profile_names(col_data, review_items)
        self._extract_star_ratings(soup, col_data)
        self._extract_titles(col_data, review_items)
        self._extract_dates(col_data, review_items)
        self._extract_reviews(col_data, review_items)
        self._extract_helpfuls(col_data, review_items)
        self._extract_config_colors(col_data, review_items)

        row_data = [[] for _ in range(self._get_max_col_length(col_data))]
        if not row_data:
            raise EmptyRowData()

        self._append_data_to_csv(
            orig_type,
            product.product_name,
            col_data,
            row_data)

    def _extract_titles(self, col_data, review_items):
        for items in review_items.find_all("a", {"data-hook": "review-title"}):
            for item in items.find_all("span", recursive=False):
                col_data["titles"].append(
                    item.get_text().encode('unicode_escape').decode())

    def _extract_dates(self, col_data, review_items):
        for item in review_items.find_all(
                "span", class_="a-size-base a-color-secondary review-date"):
            col_data["dates"].append(
                item.get_text().encode('unicode_escape').decode())

    def _extract_reviews(self, col_data, review_items):
        for item in review_items.find_all("span", {"data-hook": "review-body"}):
            col_data["reviews"].append(
                item.get_text().encode('unicode_escape').decode())

    def _extract_helpfuls(self, col_data, review_items):
        for item in review_items.find_all(
                "span", class_="a-size-base a-color-tertiary cr-vote-text"):
            col_data["helpfuls"].append(
                item.get_text().encode('unicode_escape').decode())

    def _extract_config_colors(self, col_data, review_items):
        for item in review_items.find_all(
                "a", class_="a-size-mini a-link-normal a-color-secondary"):
            col_data["config_color"].append(
                item.get_text().encode('unicode_escape').decode())

    def _extract_profile_names(self, col_data, review_items):
        for item in review_items.find_all("span", class_="a-profile-name"):
            if item.parent.parent["class"] == ['a-profile',
                                               'cr-lightbox-customer-profile']:
                continue
            col_data["names"].append(
                item.get_text().encode('unicode_escape').decode())

    def _extract_star_ratings(self, soup, col_data):
        for itemx in soup.find_all("i", {"data-hook": "review-star-rating"}):
            for item in itemx.find_all("span", class_="a-icon-alt"):
                col_data["stars"].append(
                    item.get_text().encode('unicode_escape').decode())

    def _append_data_to_csv(self, orig_type, product_name, col_data, row_data):

        for idx, row in enumerate(row_data):
            for col_name, col in col_data.items():
                if col_name in ["product_name", "base_price", "config_price", "bundle_price"]:
                    row.append(col)
                    continue
                try:
                    row.append(col[idx])
                except IndexError:
                    row.append(None)
                except TypeError:
                    row.append(col)
        try:
            self._write_to_csv(orig_type, product_name, row_data)
        except Exception:
            logging.exception("Failed to write to csv with. Retrying")
            sleep(5)
            self._write_to_csv(orig_type, product_name, row_data)

    def _write_to_csv(self, orig_type, product_name, row_data):
        with open(f"scrape_data_cwd/{orig_type}_{product_name}_{CURR_DT_TIME}.csv", "a", encoding="utf-8") as f:
            writer = csv.writer(f, lineterminator='\n')
            writer.writerows(row_data)
            logging.info("Wrote %d rows", len(row_data))

    def _get_max_col_length(self, col_data):
        return max(
            map(len, [col for col in col_data.values() if isinstance(col, list)]))


echo_3 = Product(
    product_name="Echo_Dot_3rd_Gen_2018_Charcoal",
    base_price=22.92,
    config_price=15.06,
    bundle_price=59.98,
    product_code="B0BV4S8RT5",
)

echo_4 = Product(
    product_name="Echo_4th_Gen_With_premium_sound_smart_home_hub_and_Alexa",
    base_price=61.76,
    config_price=None,
    bundle_price=None,
    product_code="B07XKF75B8",
)

echo_5 = Product(
    product_name="Echo_Dot_5th_Gen_2022_release_With_bigger_vibrant_sound_helpful_routines_and_Alexa",
    base_price=29.99,
    config_price=None,
    bundle_price=None,
    product_code="B09B8V1LZ3",
    start_pgs={
        "general": 1,
        "critical": 1,
        "positive": 1,
    }
)

echo_5_with_clock = Product(
    product_name="Echo_Dot_5th_Gen_2022_release_with_clock_Smart_speaker_with_clock_and_Alexa",
    base_price=39.99,
    config_price=None,
    bundle_price=None,
    product_code="B09B8W5FW7",
)

products_to_scrape = [
    # echo_3,
    # echo_4,
    # echo_5,
    echo_5_with_clock
]

# reviews_wanted = 1000
# max_pgs = min(int(reviews_wanted/(len(products_to_scrape)*3*10)), 500)
# global_max_pgs = {
#     "general": 500,
#     "critical": 500,
#     "positive": 500,
# }


CURR_DT_TIME = datetime.now().strftime(r'%Y%m%d%H%M%S')
# CURR_DT_TIME = "20230430211235"

logging.basicConfig(filename=f"scrape_logs/{CURR_DT_TIME}.log",
                    filemode='a',
                    format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

scraper = Scraper(
    sleep_mean=1,
    sleep_sigma=5,
    products=products_to_scrape,
    # global_max_pgs=global_max_pgs
)
scraper.scrape()
