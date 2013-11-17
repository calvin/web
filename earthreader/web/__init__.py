import datetime
import hashlib
try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

from flask import Flask, jsonify, render_template, request, url_for
from libearth.codecs import Rfc3339
from libearth.compat import binary
from libearth.crawler import CrawlError, crawl
from libearth.parser.autodiscovery import autodiscovery, FeedUrlNotFoundError
from libearth.repository import FileSystemRepository
from libearth.session import Session
from libearth.stage import Stage
from libearth.subscribe import Category, Subscription, SubscriptionList
from libearth.tz import now

from .wsgi import MethodRewriteMiddleware


app = Flask(__name__)
app.wsgi_app = MethodRewriteMiddleware(app.wsgi_app)


app.config.update(dict(
    ALLFEED='All Feeds',
    SESSION_NAME=None,
))


class IteratorNotFound(ValueError):
    """Rise when the iterator does not exist"""


class InvalidCategoryID(ValueError):
    """Rise when the category ID is not valid"""


class FeedNotFound(ValueError):
    """Rise when the feed is not reachable"""


class EntryNotFound(ValueError):
    """Rise when the entry is not reachable"""


class UnreachableUrl(ValueError):
    """Rise when the url is not reachable"""


class Cursor():

    def __init__(self, category_id, return_parent=False):
        stage = get_stage()
        self.subscriptionlist = (stage.subscriptions if stage.subscriptions
                                 else SubscriptionList())
        self.value = self.subscriptionlist
        self.path = ['/']
        self.category_id = None

        target_name = None
        self.target_child = None

        try:
            if category_id:
                self.category_id = category_id
                self.path = [key[1:] for key in category_id.split('/')]
                if return_parent:
                    target_name = self.path.pop(-1)
                for key in self.path:
                    self.value = self.value.categories[key]
                if target_name:
                    self.target_child = self.value.categories[target_name]
        except:
            raise InvalidCategoryID('The given category ID is not valid')

    def __getattr__(self, attr):
        return getattr(self.value, attr)

    def __iter__(self):
        return iter(self.value)

    def join_id(self, append):
        if self.category_id:
            return self.category_id + '/-' + append
        return '-' + append


def get_stage():
    session = Session(app.config['SESSION_NAME'])
    repo = FileSystemRepository(app.config['REPOSITORY'])
    return Stage(session, repo)


def get_hash(name):
    return hashlib.sha1(binary(name)).hexdigest()


def get_all_feeds(cursor, path=None):
    feeds = []
    categories = []
    for child in cursor:
        if isinstance(child, Subscription):
            feeds.append({
                'title': child._title,
                'entries_url': url_for(
                    'feed_entries',
                    category_id=cursor.category_id,
                    feed_id=child.feed_id,
                    _external=True
                ),
                'remove_feed_url': url_for(
                    'delete_feed',
                    category_id=cursor.category_id,
                    feed_id=child.feed_id,
                    _external=True
                )
            })
        elif isinstance(child, Category):
            categories.append({
                'title': child._title,
                'feeds_url': url_for(
                    'feeds',
                    category_id=cursor.join_id(child._title),
                    _external=True
                ),
                'entries_url': url_for(
                    'category_entries',
                    category_id=cursor.join_id(child._title),
                    _external=True
                ),
                'add_feed_url': url_for(
                    'add_feed',
                    category_id=cursor.join_id(child._title),
                    _external=True
                ),
                'add_category_url': url_for(
                    'add_category',
                    category_id=cursor.join_id(child._title),
                    _external=True
                ),
                'remove_category_url': url_for(
                    'delete_category',
                    category_id=cursor.join_id(child._title),
                    _external=True
                ),
            })
    return feeds, categories


@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')


@app.route('/feeds/', defaults={'category_id': ''})
@app.route('/<path:category_id>/feeds/')
def feeds(category_id):
    try:
        cursor = Cursor(category_id)
    except InvalidCategoryID:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    feeds, categories = get_all_feeds(cursor, category_id)
    return jsonify(feeds=feeds, categories=categories)


@app.route('/feeds/', methods=['POST'], defaults={'category_id': ''})
@app.route('/<path:category_id>/feeds/', methods=['POST'])
def add_feed(category_id):
    stage = get_stage()
    try:
        cursor = Cursor(category_id)
    except InvalidCategoryID:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    url = request.form['url']
    try:
        f = urllib2.urlopen(url)
        document = f.read()
        f.close()
    except Exception:
        r = jsonify(
            error='unreachable-url',
            message='Cannot connect to given url'
        )
        r.status_code = 400
        return r
    try:
        feed_links = autodiscovery(document, url)
    except FeedUrlNotFoundError:
        r = jsonify(
            error='unreachable-feed-url',
            message='Cannot find feed url'
        )
        r.status_code = 400
        return r
    feed_url = feed_links[0].url
    feed_url, feed, hints = next(iter(crawl([feed_url], 1)))
    sub = cursor.subscribe(feed)
    stage.subscriptions = cursor.subscriptionlist
    stage.feeds[sub.feed_id] = feed
    return feeds(category_id)


@app.route('/', methods=['POST'], defaults={'category_id': ''})
@app.route('/<path:category_id>/', methods=['POST'])
def add_category(category_id):
    stage = get_stage()
    try:
        cursor = Cursor(category_id)
    except InvalidCategoryID:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    title = request.form['title']
    outline = Category(label=title, _title=title)
    cursor.add(outline)
    stage.subscriptions = cursor.subscriptionlist
    return feeds(category_id)


@app.route('/<path:category_id>/', methods=['DELETE'])
def delete_category(category_id):
    stage = get_stage()
    try:
        cursor = Cursor(category_id, True)
    except InvalidCategoryID:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    cursor.remove(cursor.target_child)
    stage.subscriptions = cursor.subscriptionlist
    index = category_id.rfind('/')
    if index == -1:
        return feeds('')
    else:
        return feeds(category_id[:index])


@app.route('/feeds/<feed_id>/', methods=['DELETE'],
           defaults={'category_id': ''})
@app.route('/<path:category_id>/feeds/<feed_id>/', methods=['DELETE'])
def delete_feed(category_id, feed_id):
    stage = get_stage()
    try:
        cursor = Cursor(category_id)
    except InvalidCategoryID:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    target = None
    for subscription in cursor:
        if isinstance(subscription, Subscription):
            if feed_id == hashlib.sha1(
                    binary(subscription.feed_uri)).hexdigest():
                target = subscription
    if target:
        cursor.discard(target)
    else:
        r = jsonify(
            error='feed-not-found-in-path',
            message='Given feed does not exist in the path'
        )
        r.status_code = 400
        return r
    stage.subscriptions = cursor.subscriptionlist
    return feeds(category_id)


iterators = {}


def tidy_iterators_up():
    global iterators
    lists = []
    for key, (it, time_saved) in iterators.items():
        if time_saved >= now() - datetime.timedelta(minutes=30):
            lists.append((key, (it, time_saved)))
        if len(lists) >= 10:
            break
    iterators = dict(lists)


def to_bool(str):
    return str.strip().lower() == 'true'


def get_iterator(url_token):
    pair = iterators.get(url_token)
    if pair:
        it = pair[0]
        return it
    else:
        raise IteratorNotFound('The iterator does not exist')


def get_permalink(data):
    permalink = None
    for link in data.links:
        if link.relation == 'alternate' and \
                link.mimetype == 'text/html':
            permalink = link.uri
        if not permalink:
            permalink = data.id
    return permalink


@app.route('/feeds/<feed_id>/entries/', defaults={'category_id': ''})
@app.route('/<path:category_id>/feeds/<feed_id>/entries/')
def feed_entries(category_id, feed_id):
    stage = get_stage()
    try:
        Cursor(category_id)
    except InvalidCategoryID:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    try:
        feed = stage.feeds[feed_id]
    except KeyError:
        r = jsonify(
            error='feed-not-found',
            message='Given feed does not exist'
        )
        r.status_code = 404
        return r
    url_token = request.args.get('url_token')
    it = []
    if url_token:
        try:
            it = get_iterator(url_token)
        except IteratorNotFound:
            pass
    else:
        url_token = str(now())
    if not it:
        it = iter(feed.entries)
        entry_after = request.args.get('entry_after')
        if entry_after:
            entry = next(it)
            while get_hash(entry.id) == entry_after:
                entry = next(it)
    iterators[url_token] = it, now()
    entries = []
    read = request.args.get('read')
    starred = request.args.get('starred')
    feed_permalink = get_permalink(feed)
    while len(entries) < 20:
        try:
            entry = next(it)
        except StopIteration:
            iterators.pop(url_token)
            break
        entry_permalink = get_permalink(entry)
        if (read is None or to_bool(read) == bool(entry.read)) and \
                (starred is None or to_bool(starred) == bool(entry.starred)):
            entries.append({
                'title': entry.title,
                'entry_url': url_for(
                    'feed_entry',
                    category_id=category_id,
                    feed_id=feed_id,
                    entry_id=get_hash(entry.id),
                    _external=True,
                ),
                'entry_id': get_hash(entry.id),
                'permalink': entry_permalink or None,
                'updated': entry.updated_at.__str__(),
                'read': bool(entry.read) if entry.read else False,
                'starred': bool(entry.starred) if entry.starred else False,
                'feed': {
                    'title': feed.title,
                    'entries_url': url_for(
                        'feed_entries',
                        feed_id=feed_id
                    ),
                    'permalink': feed_permalink or None
                }
            })
    tidy_iterators_up()
    if len(entries) < 20:
        next_url = None
    else:
        next_url = url_for(
            'feed_entries',
            category_id=category_id,
            feed_id=feed_id,
            url_token=url_token,
            entry_after=entries[-1]['entry_id'],
            read=read,
            starred=starred
        )
    return jsonify(
        title=feed.title,
        entries=entries,
        next_url=next_url
    )


@app.route('/entries/', defaults={'category_id': ''})
@app.route('/<path:category_id>/entries/')
def category_entries(category_id):
    stage = get_stage()
    try:
        cursor = Cursor(category_id)
    except InvalidCategoryID:
        r = jsonify(
            error='category-path-invalid',
            message='Given category was not found'
        )
        r.status_code = 404
        return r
    url_token = request.args.get('url_token')
    iters = []
    if url_token:
        try:
            iters = get_iterator(url_token)
        except IteratorNotFound:
            pass
    else:
        url_token = str(now())
    read = request.args.get('read')
    starred = request.args.get('starred')
    if not iters:
        subscriptions = cursor.recursive_subscriptions
        entry_after = request.args.get('entry_after')
        if entry_after:
            time_after, _id = entry_after.split('@')
            time_after = Rfc3339().decode(time_after.replace(' ', 'T'))
        else:
            time_after, _id = None, None
            for subscription in subscriptions:
                try:
                    feed = stage.feeds[subscription.feed_id]
                except KeyError:
                    continue
                it = iter(feed.entries)
                while True:
                    try:
                        entry = next(it)
                    except StopIteration:
                        break
                    if ((time_after is None or entry.updated_at <= time_after)
                        and (_id is None or get_hash(entry.id) != _id) and
                        (read is None or to_bool(read) == bool(entry.read)) and
                        (starred is None or
                         to_bool(starred) == bool(entry.starred))):
                        item = (feed.title, get_hash(feed.id),
                                get_permalink(feed), it, entry)
                        iters.append(item)
                        break
    entries = []
    while len(entries) < 20 and iters:
        iters = \
            sorted(iters, key=lambda item: item[4].updated_at, reverse=True)
        feed_title, feed_id, feed_permalink, it, entry = iters[0]
        entry_permalink = get_permalink(entry)
        entries.append({
            'title': entry.title,
            'entry_url': url_for(
                'feed_entry',
                category_id=category_id,
                feed_id=feed_id,
                entry_id=get_hash(entry.id),
                _external=True,
            ),
            'entry_id': get_hash(entry.id),
            'permalink': entry_permalink or None,
            'updated': entry.updated_at.__str__(),
            'read': bool(entry.read) if entry.read else False,
            'starred': bool(entry.starred) if entry.starred else False,
            'feed': {
                'title': feed_title,
                'entries_url': url_for(
                    'feed_entries',
                    feed_id=feed_id
                ),
                'permalink': feed_permalink or None
            }
        })
        while True:
            try:
                entry = next(it)
            except StopIteration:
                iters.pop(0)
                break
            if ((read is None or to_bool(read) == bool(entry.read)) and
                (starred is None or
                 to_bool(starred) == bool(entry.starred))):
                item = (feed_title, feed_id, feed_permalink, it,
                        entry)
                iters[0] = item
                break
    iterators[url_token] = iters, now()
    tidy_iterators_up()
    if len(entries) < 20:
        next_url = None
    else:
        next_url = url_for(
            'category_entries',
            category_id=category_id,
            url_token=url_token,
            entry_after=entries[-1]['updated'] + '@' + entries[-1]['entry_id'],
            read=read,
            starred=starred
        )
    return jsonify(
        title=category_id.split('/')[-1][1:] or app.config['ALLFEED'],
        entries=entries,
        next_url=next_url
    )


@app.route('/feeds/<feed_id>/entries/', defaults={'category_id': ''},
           methods=['PUT'])
@app.route('/<path:category_id>/feeds/<feed_id>/entries/', methods=['PUT'])
@app.route('/entries/', defaults={'category_id': ''}, methods=['PUT'])
@app.route('/<path:category_id>/entries/', methods=['PUT'])
def update_entries(category_id, feed_id=None):
    stage = get_stage()
    try:
        cursor = Cursor(category_id)
    except InvalidCategoryID:
        r = jsonify(
            error='category-path-invalid',
            message='Given category path is not valid'
        )
        r.status_code = 404
        return r
    failed = []
    ids = {}
    if feed_id:
        for sub in cursor.recursive_subscriptions:
            if sub.feed_id == feed_id:
                ids[sub.feed_uri] = sub.feed_id
    else:
        for sub in cursor.recursive_subscriptions:
            ids[sub.feed_uri] = sub.feed_id
    it = iter(crawl(ids.keys(), 4))
    while True:
        try:
            feed_url, feed_data, crawler_hints = next(it)
        except CrawlError as e:
            failed.append(e.message)
            continue
        except StopIteration:
            break
        stage.feeds[ids[feed_url]] = feed_data
    r = jsonify(failed=failed)
    r.status_code = 202
    return r


def find_feed_and_entry(category_id, feed_id, entry_id):
    stage = get_stage()
    Cursor(category_id)
    try:
        feed = stage.feeds[feed_id]
    except KeyError:
        raise FeedNotFound('The feed is not reachable')
    feed_permalink = None
    for link in feed.links:
        if link.relation == 'alternate'\
           and link.mimetype == 'text/html':
            feed_permalink = link.uri
        if not feed_permalink:
            feed_permalink = feed.id
    for entry in feed.entries:
        entry_permalink = None
        for link in entry.links:
            if link.relation == 'alternate'\
               and link.mimetype == 'text/html':
                entry_permalink = link.uri
        if not entry_permalink:
            entry_permalink = entry.id
        if entry_id == get_hash(entry.id):
            return feed, feed_permalink, entry, entry_permalink
    raise EntryNotFound('The entry is not reachable')


@app.route('/feeds/<feed_id>/entries/<entry_id>/',
           defaults={'category_id': ''})
@app.route('/<path:category_id>/feeds/<feed_id>/entries/<entry_id>/')
def feed_entry(category_id, feed_id, entry_id):
    try:
        feed, feed_permalink, entry, entry_permalink = \
            find_feed_and_entry(category_id, feed_id, entry_id)
    except (InvalidCategoryID, FeedNotFound, EntryNotFound):
        r = jsonify(
            error='entry-not-found',
            message='Given entry does not exist'
        )
        r.status_code = 404
        return r

    content = entry.content or entry.summary
    if content is not None:
        content = content.sanitized_html

    return jsonify(
        title=entry.title,
        content=content,
        updated=entry.updated_at.__str__(),
        permalink=entry_permalink or None,
        read_url=url_for(
            'read_entry',
            category_id=category_id,
            feed_id=feed_id,
            entry_id=entry_id,
            _external=True
        ),
        unread_url=url_for(
            'unread_entry',
            category_id=category_id,
            feed_id=feed_id,
            entry_id=entry_id,
            _external=True
        ),
        star_url=url_for(
            'star_entry',
            category_id=category_id,
            feed_id=feed_id,
            entry_id=entry_id,
            _external=True
        ),
        unstar_url=url_for(
            'unstar_entry',
            category_id=category_id,
            feed_id=feed_id,
            entry_id=entry_id,
            _external=True
        ),
        feed={
            'title': feed.title,
            'entries_url': url_for(
                'feed_entries',
                feed_id=feed_id,
                _external=True
            ),
            'permalink': feed_permalink or None
        }
    )


@app.route('/feeds/<feed_id>/entries/<entry_id>/read/',
           defaults={'category_id': ''}, methods=['PUT'])
@app.route('/<path:category_id>/feeds/<feed_id>/entries/<entry_id>/read/',
           methods=['PUT'])
def read_entry(category_id, feed_id, entry_id):
    stage = get_stage()
    try:
        feed, _, entry, _ = find_feed_and_entry(category_id, feed_id, entry_id)
    except (InvalidCategoryID, FeedNotFound, EntryNotFound):
        r = jsonify(
            error='entry-not-found',
            message='Given entry does not exist'
        )
        r.status_code = 404
        return r
    entry.read = True
    stage.feeds[feed_id] = feed
    return jsonify()


@app.route('/feeds/<feed_id>/entries/<entry_id>/read/',
           defaults={'category_id': ''}, methods=['DELETE'])
@app.route('/<path:category_id>/feeds/<feed_id>/entries/<entry_id>/read/',
           methods=['DELETE'])
def unread_entry(category_id, feed_id, entry_id):
    stage = get_stage()
    try:
        feed, _, entry, _ = find_feed_and_entry(category_id, feed_id, entry_id)
    except (InvalidCategoryID, FeedNotFound, EntryNotFound):
        r = jsonify(
            error='entry-not-found',
            message='Given entry does not exist'
        )
        r.status_code = 404
        return r
    entry.read = False
    stage.feeds[feed_id] = feed
    return jsonify()


@app.route('/feeds/<feed_id>/entries/<entry_id>/star/',
           defaults={'category_id': ''}, methods=['PUT'])
@app.route('/<path:category_id>/feeds/<feed_id>/entries/<entry_id>/star/',
           methods=['PUT'])
def star_entry(category_id, feed_id, entry_id):
    stage = get_stage()
    try:
        feed, _, entry, _ = find_feed_and_entry(category_id, feed_id, entry_id)
    except (InvalidCategoryID, FeedNotFound, EntryNotFound):
        r = jsonify(
            error='entry-not-found',
            message='Given entry does not exist'
        )
        r.status_code = 404
        return r
    entry.starred = True
    stage.feeds[feed_id] = feed
    return jsonify()


@app.route('/feeds/<feed_id>/entries/<entry_id>/star/',
           defaults={'category_id': ''}, methods=['DELETE'])
@app.route('/<path:category_id>/feeds/<feed_id>/entries/<entry_id>/star/',
           methods=['DELETE'])
def unstar_entry(category_id, feed_id, entry_id):
    stage = get_stage()
    try:
        feed, _, entry, _ = find_feed_and_entry(category_id, feed_id, entry_id)
    except (InvalidCategoryID, FeedNotFound, EntryNotFound):
        r = jsonify(
            error='entry-not-found',
            message='Given entry does not exist'
        )
        r.status_code = 404
        return r
    entry.starred = False
    stage.feeds[feed_id] = feed
    return jsonify()
