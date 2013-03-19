# generic.py
# Functions related to all pages.
# Every source page should import this.

import webapp2 
import jinja2
import os, re, string, hashlib, logging
from google.appengine.ext import db, blobstore
from google.appengine.ext.webapp import blobstore_handlers

import filters

template_dir = os.path.join(os.path.dirname(__file__), '../templates')
jinja_env = jinja2.Environment(loader = jinja2.FileSystemLoader(template_dir), autoescape = True)

SALT_LENGTH = 16
DOMAIN_PREFIX = "http://research-engine.appspot.com"

jinja_env.filters['wikify'] = filters.wikify
jinja_env.filters['md'] = filters.md


##########################
##   Helper Functions   ##
##########################

def render_str(template, **params):
    t = jinja_env.get_template(template)
    return t.render(params)

# Hashing
def make_salt(length = SALT_LENGTH):
    assert length > 0
    return ''.join(string.lowercase[ord(os.urandom(1)) % 26] for x in range(length))

def hash_str(s):
    return hashlib.sha256(s).hexdigest()

def make_secure_val(val, salt):
    return "%s|%s" % (val, hash_str(val + salt))

def get_secure_val(h, salt):
    val = h.split("|")[0]
    if h == make_secure_val(val, salt):
        return val
    return None



###########################
##   Datastore Objects   ##
###########################

# User related stuff.

class UnverifiedUsers(db.Model):
    username = db.StringProperty(required = True)
    email = db.EmailProperty(required = True)
    salt = db.StringProperty(required = True)
    password_hash = db.TextProperty(required = True)


class RegisteredUsers(db.Model):
    username = db.StringProperty(required = True)
    password_hash = db.TextProperty(required = True)
    salt = db.StringProperty(required = True)
    email = db.EmailProperty(required = False)
    about_me = db.TextProperty(required = False)
    google_userid = db.StringProperty(required = False)
    my_projects = db.ListProperty(db.Key)                   # keys to Projects (defined in projects.py)
    my_notebooks = db.ListProperty(db.Key)                  # keys tootebooks (defined in notebooks.py)
    following = db.ListProperty(db.Key)

    def list_of_projects(self):
        projects_list = []
        for p_key in self.my_projects:
            project = db.Query().filter("__key__ =", p_key).get()
            if project: 
                projects_list.append(project)
            else:
                logging.warning("RegisteredUser with key (%s) contains a broken reference to project %s" 
                                % (self.key(), p_key))
        if len(projects_list) > 0:
            projects_list.sort(key=lambda p: p.last_updated, reverse=True)
        return projects_list


# Each Notification should have as parent a RegisteredUser
class EmailNotifications(db.Model):
    author = db.ReferenceProperty(required = False)
    category = db.StringProperty(required = True)
    title = db.StringProperty(required = True)
    content = db.TextProperty(required = True)
    date = db.DateTimeProperty(auto_now_add = True)
    link = db.StringProperty(required = False)       # Absolute link
    sent = db.BooleanProperty(required = True)


######################
##   Web Handlers   ##
######################

class GenericPage(webapp2.RequestHandler):

    # Querying the Datastore
    def log_read(self, dbmodel, message = ''):
        logging.debug("DB READ: Handler %s requests an instance of %s. %s"
                      % (self.__class__.__name__, dbmodel.__name__, message))
        return

    def get_item_from_key(self, instance_key, message = ''):
        item = db.Query().filter("__key__ =", instance_key).get()
        self.log_read(item.__class__, "From key. " + message)
        return item

    def get_item_from_key_str(self, key_str, message = ''):
        try:
            item = self.get_item_from_key(db.Key(key_str), message)
        except db.BadKeyError:
            item = None
        return item
    
    # Writing the Datastore
    def log_and_put(self, instance, message = ''):
        logging.debug("DB WRITE: Handler %s is writing an instance of %s. %s"
                      % (self.__class__.__name__, instance.__class__.__name__, message))
        instance.put()
        return

    def log_and_delete(self, instance, message = ''):
        logging.debug("DB WRITE: Handler %s is deleting an instance of %s. %s"
                      % (self.__class__.__name__, instance.__class__.__name__, message))
        instance.delete()
        return

    def add_notifications(self, item, users_to_notify, author, relative_link):
        for u in users_to_notify:
            notification = EmailNotifications(author = author, category = item.__class__.__name__,
                                              title = item.title, content = item.content, 
                                              link = DOMAIN_PREFIX + relative_link, sent = False, parent = u)
            self.log_and_put(notification)
        return


    # Cookies
    def get_cookie_val(self, cookie_name, salt):
        cookie = self.request.cookies.get(cookie_name)
        if cookie:
            val = get_secure_val(cookie, salt)
            return val
        return None

    def set_cookie(self, name, value, salt, path = "/"):
        cookie = "%s=%s; Path=%s" % (name, make_secure_val(value, salt), path)
        self.response.headers.add_header('Set-Cookie', str(cookie))
        return

    def remove_cookie(self, name, path = "/"):
        cookie = self.request.cookies.get(name)
        if cookie:
            del_cookie = "%s=; Path=%s" % (name, path)
            self.response.headers.add_header('Set-Cookie', str(del_cookie))
            return True
        return False

    # Users
    def get_login_user(self):
        cookie = self.request.cookies.get("username")
        if not cookie: return None
        cookie_username = cookie.split("|")[0]
        self.log_read(RegisteredUsers, "Getting logged in user. ")
        u = RegisteredUsers.all().filter("username =", cookie_username).get()
        if not u: return None
        if not get_secure_val(cookie, u.salt): return None
        return u

    def get_user_by_username(self, username, logmessage = ''):
        logging.debug("DB READ: Handler %s requests an instance of RegisteredUsers. %s"
                      % (self.__class__.__name__, logmessage))
        return RegisteredUsers.all().filter("username =", username).get()

    def get_user_by_email(self, username, logmessage = ''):
        logging.debug("DB READ: Handler %s requests an instance of RegisteredUsers. %s"
                      % (self.__class__.__name__, logmessage))
        return RegisteredUsers.all().filter("email =", username).get()
        

    # Rendering
    def write(self, *a, **kw):
        self.response.out.write(*a, **kw)

    def render_str(self, template, **params):
        return render_str(template, **params)

    def render(self, template, **kw):
        kw['user'] = self.get_login_user()
        if kw['user'] is None:
            kw['login_message'] = ('<a href="/login">Login</a> <ul><li><a href="/signup">Signup</a></li></ul>')
        else:
            kw['login_message'] = ('<a href="/%s">%s</a> <ul><li><a href="/logout">Logout</a></li></ul>' % (kw['user'].username, kw['user'].username.title()))            
        self.write(self.render_str(template, **kw))


class GenericBlobstoreUpload(blobstore_handlers.BlobstoreUploadHandler):
    # Querying the Datastore
    def log_read(self, dbmodel, message = ''):
        logging.debug("DB READ: Handler %s requests an instance of %s. %s"
                      % (self.__class__.__name__, dbmodel.__name__, message))
        return

    # Users
    def get_login_user(self):
        cookie = self.request.cookies.get("username")
        if not cookie: return None
        cookie_username = cookie.split("|")[0]
        self.log_read(RegisteredUsers, "Getting logged in user. ")
        u = RegisteredUsers.all().filter("username =", cookie_username).get()
        if not u: return None
        if not get_secure_val(cookie, u.salt): return None
        return u

    def get_user_by_username(self, username, logmessage = ''):
        logging.debug("DB READ: Handler %s requests an instance of RegisteredUsers. %s"
                      % (self.__class__.__name__, logmessage))
        return RegisteredUsers.all().filter("username =", username).get()

