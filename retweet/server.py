from wsgiref.simple_server import make_server

from . import rephrase, templates, utils, threader
import firebase_admin
from firebase_admin import firestore

import abc
import datetime
import falcon
import requests


firebase_admin.initialize_app()

db = firestore.Client(project="social-investments-337201")


class AbstractPostResrourceUnderRecaptcha(abc.ABC):

    def __init__(self):
        self.enable_recaptcha = False

    def verify_recaptcha(self, token):
        data = {
            'secret': utils.get_recaptcha_secret(),
            'response': token
        }
        r = requests.post('https://www.google.com/recaptcha/api/siteverify', data=data)
        result = r.json()
        return result['success']

    def on_post(self, req, resp):
        req_obj = req.get_media()
        if not self.ignore_recaptcha(req):
            if not "recaptcha_token" in req_obj:
                resp.status = falcon.HTTP_403
                return
            if not self.verify_recaptcha(req_obj["recaptcha_token"]):
                resp.status = falcon.HTTP_403
                return
        return self.on_post_protected(req, resp)

    def ignore_recaptcha(self, req):
        remote_ip = req.env.get("HTTP_X_FORWARDED_FOR") or req.remote_addr
        if remote_ip == "67.170.251.117" or remote_ip == "127.0.0.1":
            return True
        return not self.enable_recaptcha

    @abc.abstractmethod
    def on_post_protected(self, req, resp):
        pass


class ThreaderResource(AbstractPostResrourceUnderRecaptcha):

    def on_post_protected(self, req, resp):
        """Handles POST requests"""
        req_obj = req.get_media()
        original_text = req_obj["text"]
        if len(original_text) > 3000:
            resp.status = falcon.HTTP_400
            return
        if not original_text:
            resp.status = falcon.HTTP_400
            return

        updated_text = threader.rethread(original_text)
        db.collection("threads").add({
            "timestamp": firestore.SERVER_TIMESTAMP,
            "text": original_text,
            "thread": updated_text
        })
        resp.status = falcon.HTTP_200
        resp.media = {"thread": updated_text}


class RephraseResource(AbstractPostResrourceUnderRecaptcha):

    def on_post_protected(self, req, resp):
        """Handles POST requests"""
        req_obj = req.get_media()
        original_text = req_obj["text"]
        template_name = templates.get_default_template_name()
        if "template_name" in req_obj:
            template_name = req_obj["template_name"]
        if not original_text:
            resp.status = falcon.HTTP_403
            return

        updated_text = rephrase.rephrase(original_text, template_name=template_name)
        db.collection("retweet").add({
            "timestamp": firestore.SERVER_TIMESTAMP,
            "template_name": template_name,
            "text": original_text,
            "rephrased_text": updated_text
        })
        resp.status = falcon.HTTP_200
        resp.media = {"text": updated_text}


class TemplatesResource(object):

    def __init__(self):
        self._template_names = None
        self._cache_last_updated = None

    def _maybe_update_cache(self):
        current_time = datetime.datetime.now()
        if self._cache_last_updated and (current_time - self._cache_last_updated).days < 1:
            return
        
        self._template_names = templates.get_templates_names()
        self._cache_last_updated = current_time

    def on_get(self, req, resp):
        """Handles GET requests"""
        self._maybe_update_cache()
        resp.status = falcon.HTTP_200
        resp.media = self._template_names


class TemplateResource(object):

    def on_get(self, req, resp, template_id):
        """Handles GET requests"""

        # Currnetly we are only going to return DEFAULT teamplate name
        if template_id != "DEFAULT":
            resp.status = falcon.HTTP_404
            return
        resp.status = falcon.HTTP_200
        resp.media = templates.get_teamplate_raw_string(template_id)


app = falcon.App(cors_enable=True)

rephrase_resource = RephraseResource()
templates_resource = TemplatesResource()
template_resource= TemplateResource()
threaded_resource = ThreaderResource()

app.add_route("/threadit", threaded_resource)
app.add_route("/rephrase", rephrase_resource)
app.add_route("/templates", templates_resource)
app.add_route("/templates/{template_id}", template_resource)


if __name__ == "__main__":
    with make_server("", 8080, app) as httpd:
        print("Serving on port 8080...")

        # Serve until process is killed
        httpd.serve_forever()
