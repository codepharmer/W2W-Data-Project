import math
import os
import re
import threading
from datetime import datetime
from listener import *
from functools import reduce
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.events import EventFiringWebDriver, AbstractEventListener


scraping_mutex = threading.Lock()
data_logging_mutex = threading.Lock()


class Scraper:
    def __init__(self, employees, positions, year, orig_start_date, orig_end_date):
        self.employees = employees
        self.positions = positions
        self.year = orig_start_date.year + year
        self.start_date, self.end_date, self.num_months = self.__get_timespan(orig_start_date, orig_end_date, year)
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        self.driver = webdriver.Chrome(executable_path=r'C:/bin/chromedriver.exe', options=chrome_options)


    def __get_timespan(self, orig_start_date, orig_end_date, year):
        num_years = orig_end_date.year - orig_start_date.year
        if num_years == 0:
            num_months = orig_end_date.month - orig_start_date.month + 1
            return orig_start_date, orig_end_date, num_months
        if year == 0:
            end_date = datetime.strptime(f'{orig_start_date.year + year}-12-31', '%Y-%m-%d').date()
            num_months = 13 - orig_start_date.month
            return orig_start_date, end_date,  num_months
        if year == num_years:
            start_date = datetime.strptime(f'{orig_end_date.year}-01-01', '%Y-%m-%d').date()
            num_months = orig_end_date.month
            return start_date, orig_end_date, num_months
        else:
            start_date = datetime.strptime(f'{orig_start_date.year + year}-01-01', '%Y-%m-%d').date()
            end_date = datetime.strptime(f'{orig_start_date.year + year}-12-31', '%Y-%m-%d').date()
            return start_date, end_date, 12


    # For the first and last months it's necessary to find which box in the W2W calendar the
    # first/last day is located in since can't just indiscriminately scrape the entire month.
    def __get_date_locus(self, date):
        first_day_of_month = datetime.strptime(f'{date.year}-{date.month}-01', '%Y-%m-%d').date()
        weekday = (first_day_of_month.weekday() + 1) % 7 #weekday() returns Monday as 0 but we want Sunday to be 0
        start_box = date.day + weekday - 1 #-1 because otherwise the first day of the month would be double counted
        row = 2 + (math.floor(start_box / 7) * 2)
        column = start_box - (math.floor(start_box / 7) * 7)
        return row, column


    # URL will be formatted https://...&Date=YYYY-MM-DD
    def __update_url(self, url):
        url_base, date = url.split('&Date=')
        year, month, day = date.split('-')
        return f'{url_base}&Date={year}-{int(month)+1}-{day}'


    def scrape_year(self, login_function):
        sid = login_function(self.driver)
        start_row, start_column = self.__get_date_locus(self.start_date)
        end_row, end_column = self.__get_date_locus(self.end_date)
        days = (self.end_date - self.start_date).days + 1 # +1 because dates are inclusive

        edriver = EventFiringWebDriver(self.driver, MyListener(self.num_months, days, start_row, start_column, end_row, end_column))


        url = f'https://www8.whentowork.com/cgi-bin/w2wJ.dll/empfullschedule?SID={sid}&lmi=&View=Month&Date={self.start_date.strftime("%Y-%m-%d")}'
        for i in range(self.num_months):
            scraping_mutex.acquire()
            edriver.get(url)
            scraping_mutex.release()
            url = self.__update_url(url)
        self.scraped_data = edriver._listener.scraped_data


    def __get_employee_name(self, entry):
        if '(deleted)' in entry:
            employee_name = entry.split(' (deleted)')[0]
        else:
            employee_name = list(filter(lambda x: x in entry, self.employees))
            if len(employee_name) == 1:
                employee_name = employee_name[0]

        return employee_name.replace('\xa0', '')


    def __as_time_object(self,  time_str):
        try:
            time_obj = datetime.strptime(time_str, '%I%p')
        except:
            time_obj = datetime.strptime(time_str, '%I:%M%p')
        return time_obj


    def __get_time(self, entry, pattern):
        start_time = pattern.search(entry).group(1)
        end_time = pattern.search(entry).group(4)
        timedelta =  self.__as_time_object(end_time) - self.__as_time_object(start_time)
        return timedelta.seconds / 3600 # return in hours


    def analyze_results(self, results):
        pattern = re.compile('([1-9][0-9]?(:[0-9]{2})?(a|p)m) - ([1-9][0-9]?(:[0-9]{2})?(a|p)m)')

        scraped_data = self.scraped_data.split('\n')
        for entry in scraped_data:
            if entry in self.positions:
                position = entry
            # Don't include hours that were spent training to learn a new position
            # If the first scraped month contains no classes, position will not be defined
            elif 'position' in locals() and position.startswith('BK Training'): #
                continue
            elif pattern.match(entry):
                hours = self.__get_time(entry, pattern)
            # Active employees will be in self.employees; deleted employees will have their name
            # followed by (deleted).  Active employees will often have their name followed by the
            # working location (eg. '- P40') so it's not enough so simply write 'entry in self.employees'
            elif '(deleted)' in entry or reduce(lambda acc, x: acc or x in entry, self.employees, False):
                employee = self.__get_employee_name(entry)
                data_logging_mutex.acquire()
                if employee not in results[self.year]:
                    results[self.year][employee] = hours
                else:
                    results[self.year][employee] += hours
                data_logging_mutex.release()
            # Don't include hours that were spent shadowing another employee
            elif 'shadow' in entry.lower():
                data_logging_mutex.acquire()
                results[self.year][employee] -= hours
                if results[self.year][employee] == 0:
                    del results[self.year][employee]
                data_logging_mutex.release()
            else:
                continue





