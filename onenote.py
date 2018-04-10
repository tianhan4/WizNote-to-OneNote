#!/usr/bin/env python3

import mimetypes
import os
import re
import sqlite3
from collections import namedtuple, OrderedDict
from datetime import datetime, timedelta, timezone
from sys import platform
from urllib.parse import urlencode
from zipfile import ZipFile
errorFile = []
import requests
from bs4 import BeautifulSoup
from requests import HTTPError
from requests.auth import AuthBase

CLIENT_ID = 'ddbd41a5-c46d-44f2-85b2-d73bbd7bee7d'
CLIENT_SECRET = 'qqgu6ApNZyUmvYgna2WBwK5'
REDIRECT_URI = 'https://login.live.com/oauth20_desktop.srf'

LINUX_DATA_PATH = os.path.expanduser('~/.wiznote/{}/data')
API_BASE = 'https://www.onenote.com/api/v1.0/me/notes'
AUTH_URL = 'https://login.live.com/oauth20_authorize.srf?' + urlencode({
    'response_type': 'code',
    'client_id': CLIENT_ID,
    'redirect_uri': REDIRECT_URI,
    'scope': 'wl.offline_access office.onenote_create'
})
OAUTH_URL = 'https://login.live.com/oauth20_token.srf'

Document = namedtuple('Document', ['guid', 'title', 'location', 'name', 'url', 'created'])


class BearerAuth(AuthBase):
    def __init__(self, token):
        self.token = token

    def __call__(self, r):
        r.headers['Authorization'] = 'Bearer ' + self.token
        return r


def get_data_dir():
    while True:
        if platform == 'linux':
            account = input('Input WizNote account: ')
            data_path = LINUX_DATA_PATH.format(account)
        elif platform == 'win32' or platform == 'darwin':
            #data_path = input('Input WizNote dir path of "index.db": ')
            data_path=r"C:\Users\henrysky\Documents\My Knowledge\Data\tianhan_4@aliyun.com"
        else:
            raise Exception('Unsupported platform')

        index_path = os.path.join(data_path, 'index.db')
        if not os.path.isfile(index_path):
            print('Account data not found!')
            continue

        break

    return data_path, index_path


def get_doc_path(data_path, doc):
    if platform == 'linux' or platform == 'darwin':
        return os.path.join(data_path, 'notes', '{%s}' % doc.guid)

    if platform == 'win32':
        return os.path.join(data_path, doc.location.strip('/'), doc.name)

    raise Exception('Unsupported platform')


def get_token(session):
    print('Sign in via: ')
    print(AUTH_URL)
    print('Copy the url of blank page after signed in,\n'
          'url should starts with "https://login.live.com/oauth20_desktop.srf"')

    while True:
        url = input('URL: ')

        match = re.match(r'https://login.live.com/oauth20_desktop\.srf\?code=([\w-]{37})', url)
        if not match:
            print('Invalid URL!')
            continue

        code = match.group(1)
        break

    resp = session.post(OAUTH_URL, data={
        'grant_type': 'authorization_code',
        'client_id': CLIENT_ID,
        'client_secret': '',
        'code': code,
        'redirect_uri': REDIRECT_URI,
    }).json()

    return resp['access_token'],resp['refresh_token']


def create_notebook(session):
    name = input('Pleas input new notebook name(such as WizNote, can\'t be empty).\nName: ')
    print('Creating notebook: "%s"' % name)
    resp = session.post(API_BASE + '/notebooks', json={'name': name})
    resp.raise_for_status()

    return resp.json()['id']


def create_section(session, notebook_id, name, group_id):
    print('Creating section: "%s"' % name)
    if group_id:
        resp = session.post(API_BASE + '/sectionGroups/%s/sections' % group_id, json={'name': name})
    else:
        resp = session.post(API_BASE + '/notebooks/%s/sections' % notebook_id, json={'name': name})
    resp.raise_for_status()

    return resp.json()['id']


def create_section_group(session, notebook_id, name, group_id):
    print('Creating section group: "%s"' % name)
    if group_id:
        resp = session.post(API_BASE + '/sectionGroups/%s/sectionGroups' % group_id, json={'name': name})
    else:
        resp = session.post(API_BASE + '/notebooks/%s/sectionGroups' % notebook_id, json={'name': name})

    resp.raise_for_status()

    return resp.json()['id']

def get_documents():
    data_path, index_path = get_data_dir()

    result = OrderedDict()
    with sqlite3.connect(index_path) as conn:
        sql = """
        SELECT DOCUMENT_GUID, DOCUMENT_TITLE, DOCUMENT_LOCATION, DOCUMENT_NAME, DOCUMENT_URL, DT_CREATED,
          DOCUMENT_PROTECT, DOCUMENT_ATTACHEMENT_COUNT
        FROM wiz_document
        ORDER BY DOCUMENT_LOCATION;
        """.strip()

        cur = conn.execute(sql)
        while True:
            row = cur.fetchone()
            if not row:
                break

            guid, title, location, name, url, created, protect, attachment_count = row

            if protect:
                print('Ignore protected document "%s"' % (location + title))
                continue

            if attachment_count:
                print('Ignore %d attachment(s) in "%s"' % (attachment_count, location + title))

            docs = result.get(location)
            if not docs:
                docs = []
                result[location] = docs

            doc = Document(guid, title, location, name, url, created)
            docs.append(doc)

    return data_path, result


def clean_html(data, doc):
    def parse_datetime(time_str):
        time = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        return time.replace(tzinfo=timezone(timedelta(hours=-8))).isoformat()

    soup = BeautifulSoup(data, 'lxml')

    pattern = re.compile(r'^index_files/(.+?)$')
    imgs = soup.find_all('img', src=pattern)

    src_file_names = []
    for img in imgs:
        src = img['src']
        match = pattern.fullmatch(src)
        file_name = match.group(1)

        src_file_names.append(file_name)
        img['src'] = 'name:' + file_name

    head_tag = soup.head
    if not head_tag:
        head_tag = soup.new_tag('head')
        soup.html.insert(0, head_tag)

    title_tag = head_tag.title
    if not title_tag:
        title_tag = soup.new_tag('title')

    # to avoid 'Untitled' in title
    title_tag.string = doc.title
    head_tag.insert(0, title_tag)

    created_tag = soup.new_tag('meta', attrs={'name': 'created', 'content': parse_datetime(doc.created)})
    head_tag.insert(1, created_tag)

    if doc.url:
        url_tag = soup.new_tag('p')
        url_tag.string = 'URL: ' + doc.url
        soup.body.insert(0, url_tag)

    return soup.encode('utf-8'), src_file_names


def upload_doc(session, section_id, data_path, doc):
        doc_path = get_doc_path(data_path, doc)

        print('Processing %s%s (%s)' % (doc.location, doc.title, doc.guid))

        with ZipFile(doc_path) as zip_file:
            html_content, src_file_names = clean_html(zip_file.read('index.html'), doc)

            if len(src_file_names) > 5:
                print('Upload may failed if images more than 5')

            data_send = {
                'Presentation': (None, html_content, mimetypes.guess_type('index.html')[0])
            }

            for name in src_file_names:
                data_send[name] = (None, zip_file.read('index_files/' + name), mimetypes.guess_type(name)[0])

        resp = session.post(API_BASE + '/sections/%s/pages' % section_id, files=data_send)
        resp.raise_for_status()


def main():
    data_dir, docs = get_documents()

    with requests.session() as session:
        access_token, refresh_token = get_token(session)
        session.auth = BearerAuth(access_token)

        notebook_id = create_notebook(session)
        items = list(docs.items())
        groupDict = dict()

        for i in range(len(items)):
            if i%20 == 0:
                print("refresh token...")
                print(access_token, refresh_token)
                resp = session.post(OAUTH_URL, data={
                    'grant_type': 'refresh_token',
                    'client_id': CLIENT_ID,
                    'client_secret': '',
                    'redirect_uri': REDIRECT_URI,
                    'refresh_token': refresh_token,
                }).json()
                access_token, refresh_token = resp['access_token'],resp['refresh_token']
                session.auth = BearerAuth(access_token)
                print(access_token, refresh_token)
            try:
                allName = items[i][0].strip('/')
                sectionHierarchy = allName.split('/')
                section_name = sectionHierarchy[-1]
                group_id = ""

                if len(sectionHierarchy) > 1:
                    for j in range(1,len(sectionHierarchy)):
                        group_name = "/".join(sectionHierarchy[:j])
                        group_id = groupDict.get(group_name)
                        if group_id is None:
                            if j>1:
                                father_group_id = groupDict["/".join(sectionHierarchy[:j-1])]
                            else:
                                father_group_id = None
                            group_id = create_section_group(session, notebook_id, sectionHierarchy[j-1], father_group_id)
                            groupDict[group_name] = group_id
                else:
                    group_id = None
                print("allName: ", allName)
                print("sectionHierarchy: ",  sectionHierarchy)
                print("section_name: ", section_name)
                if i != len(items)-1 and items[i][0] in items[i+1][0]:
                    # construct section group
                    section_group_id = create_section_group(session, notebook_id, section_name, group_id)
                    unsorted_section_id = create_section(session, notebook_id, "unsorted", section_group_id)
                    groupDict[allName] = section_group_id
                    for doc in items[i][1]:
                        upload_doc(session, unsorted_section_id, data_dir, doc)
                else:
                    section_id = create_section(session, notebook_id, section_name, group_id)
                    for doc in items[i][1]:
                        upload_doc(session, section_id, data_dir, doc)

            except Exception as e:
                errorFile.append([doc.location + doc.title, e])
                print(e)


if __name__ == '__main__':
    try:
        main()
        file = open("errorFiles.txt","w")
        file.write(str(errorFile))
        file.close()
    except HTTPError as e:
        print(e.response.json())
