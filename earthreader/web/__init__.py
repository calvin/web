import glob
import hashlib
import os.path
try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

from libearth.compat import text, binary
from libearth.feed import Feed
from libearth.feedlist import Feed as OutLine, FeedList
from libearth.parser.autodiscovery import autodiscovery, FeedUrlNotFoundError
from libearth.parser.heuristic import get_document_type
from libearth.schema import read, write

from flask import Flask, abort, jsonify, request, url_for


app = Flask(__name__)


app.config.update(dict(
    repository='repo/',
    opml='earthreader.opml'
))


@app.route('/feeds/', methods=['GET'])
def feeds():
    REPOSITORY = app.config['repository']
    feedlist = glob.glob(REPOSITORY+'*')
    feeds = []
    for xml in feedlist:
        if not xml.endswith('.xml'):
            continue
        with open(xml) as f:
            feed = read(Feed, f)
            feeds.append({
                'title': text(feed.title),
                'feed_url': url_for(
                    'entries',
                    feed_id=hashlib.sha1(text(feed.id)).hexdigest(),
                    _external=True)
                })
    return jsonify(feeds=feeds)


@app.route('/feeds/', methods=['POST'])
def add_feed():
    REPOSITORY = app.config['repository']
    OPML = app.config['opml']
    if not os.path.exists(REPOSITORY + OPML):
        if not os.path.isdir(REPOSITORY):
            os.mkdir(REPOSITORY)
        feed_list = FeedList()
    else:
        feed_list = FeedList(REPOSITORY + OPML)
    url = request.form['url']
    f = urllib2.urlopen(url)
    document = f.read()
    try:
        feed_url = autodiscovery(document, url)
    except FeedUrlNotFoundError:
        return 'error'
    if not feed_url == url:
        f.close()
        f = urllib2.urlopen(feed_url)
        xml = f.read()
    else:
        xml = document
    format = get_document_type(xml)
    result = format(xml, feed_url)
    feed = result[0]
    for link in feed.links:
            if link.relation == 'alternate' and link.mimetype == 'text/html':
                blog_url = link.uri
    outline = OutLine('atom', feed.title.value, feed_url, blog_url)
    feed_list.append(outline)
    feed_list.save_file(REPOSITORY + OPML)
    file_name = hashlib.sha1(binary(feed_url)).hexdigest() + '.xml'
    with open(os.path.join(REPOSITORY, file_name), 'w') as f:
        for chunk in write(feed, indent='    ', canonical_order=True):
            f.write(chunk)
    return 'success'


@app.route('/feeds/<feed_id>/')
def entries(feed_id):
    REPOSITORY = app.config['repository']
    try:
        with open(os.path.join(REPOSITORY, feed_id + '.xml')) as f:
            feed = read(Feed, f)
            entries = []
            for entry in feed.entries:
                entries.append({
                    'title': text(entry.title),
                    'entry_url': url_for(
                        'entry',
                        feed_id=feed_id,
                        entry_id=hashlib.sha1(binary(entry.id)).hexdigest(),
                        _external=True
                    )
                })
        return jsonify(entries=entries)
    except IOError:
        abort(404)


@app.route('/feeds/<feed_id>/<entry_id>/')
def entry(feed_id, entry_id):
    pass
