import ast
import cPickle
import glob
import inspect
import os
import json
import sqlite3
import zipfile

import numpy as np
from pandas.io.json import read_json
import pysal as ps
from flask import Flask, jsonify, request, g
from werkzeug.utils import secure_filename

import fiona #Yeah - I punked out...

from api import checktypes, funcs, CustomJsonEncoder



#Make the Flask App
app = Flask(__name__)
#Setup a cache to store transient python objects

#Upload Setup
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = set(['shp', 'dbf', 'shx', 'prj', 'zip'])
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


def update_file_list(UPLOADED_FOLDER):
    """
    Globs the upload directory and get s listing of the available files.

    Parameters
    -----------
    UPLOAD_FOLDER   (str) Path supplied on launch to the upload directory.
    """
    return set([os.path.basename(i) for i in glob.glob(UPLOAD_FOLDER + '/*')])

UPLOADED_FILES = update_file_list(UPLOAD_FOLDER)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect('test.db')
    return db

def allowed_file(filename):
    """
    Check that the uploaded file extensions is in the approved list.

    Parameters
    -----------
    filename    (str) The filename, with extension

    Returns
    --------
    Boolean     (bool) True if accepted, else false
    """
    return '.' in filename and \
            filename.rsplit('.',1)[1] in ALLOWED_EXTENSIONS

def unzip(filename, path):
    """
    Safe file unzip function.
    """
    with zipfile.ZipFile(filename) as zf:
        for m in zf.infolist():
            words = m.filename.split('/')
            destination = path
            for w in words[:-1]:
                drive, w = os.path.splitdrive(w)
                head, w = os.path.split(w)
                if w in (os.curdir, os.pardir, ''):
                    continue
                destination = os.path.join(path, w)
            zf.extract(m, destination)
    return

@app.route('/', methods=['GET'])
def home():
    response = {'status':'success','data':{}}
    response['data']['links'] = [{'id':'api', 'href':'/api/'},
                                 {'id':'listdata', 'href':'/listdata/'},
                                 {'id':'upload', 'href':'/upload/'},
                                 {'id':'cached', 'href':'/cached/'}]
    return jsonify(response)

@app.route('/cached/', methods=['GET'])
def get_cached():
    response = {'status':'success','data':{'cacheditems':{}}}
    cacheditems = response['data']['cacheditems']
    cur = get_db().cursor()
    cur.execute("select * from WObj")
    result = cur.fetchall()
    for row in result:
        cacheditems[row[0]] = {'id':row[0],
                        'source':row[3],
                        'type':row[1],
                        'href':'/cached/{}/'.format(row[0])}
    return jsonify(response)

@app.route('/cached/<cachedid>/', methods=['GET'])
def get_cached_entry(cachedid):
    """
    Using the database Id (Unique) add a URL endpoint for all cached
    items.  This queries the DB for the item, reconstructs the
    binary blob into a python object, and introspects for available
    methods and attributes.
    """
    response = {'status':'success','data':{'Unable to parse available methods.'}}
    query = "SELECT Obj FROM WObj WHERE ID = {}".format(cachedid)
    cur = get_db().cursor().execute(query)
    result = cur.fetchone()[0]
    obj = cPickle.loads(str(result))

    #Check if the OBJ is a W Object
    methodlist = {}
    if isinstance(obj, ps.W):
        response['data'] = {'methods': {}, 'attrs': []}
        #Parse the method list
        methods = inspect.getmembers(obj, predicate=inspect.ismethod)
        for m in methods:
            if m[0][0] != '_':
                methodlist[m[0]] = inspect.getargspec(m[1])
        response['data']['methods'] = methodlist
        #Parse the available attributes
        attrs = inspect.getmembers(obj,lambda i : not(inspect.ismethod(i)))
        for a in attrs:
            if not a[0].startswith('__'):
                response['data']['attrs'].append(a[0])
    return jsonify(response)

@app.route('/cached/<cachedid>/<method>/', methods=['POST'])
def update_db(cachedid, method):
    """
    Load an object from the DB and call one of its methods.

    Example
    -------
    Assuming that a shapefile W Obj occupies a DB row with ID 3, row
    standardization can be applied and the object updated in the database
    using a POST request:

    curl -i -H "Content-Type: application/json" -X POST -d '{"args":["r"]}' http://localhost:5000/cached/3/set_transform/

    This example uses the flask development server on port 5000.

    Then to confirm that this worked, inspect the weights attribute via a browser:
    http://localhost:5000/cached/3/weights/

    To revert to binary:
    curl -i -H "Content-Type: application/json" -X POST -d '{"args":["b"]}' http://localhost:5000/cached/3/set_transform/
    """
    if request.json:
        response = {'status':'success','data':{}}

        query = "SELECT Obj FROM WObj WHERE ID = {}".format(cachedid)
        cur = get_db().cursor().execute(query)
        result = cur.fetchone()[0]
        obj = cPickle.loads(str(result))

        #Parse the method list
        methods = inspect.getmembers(obj, predicate=inspect.ismethod)
        for m in methods:
            if m[0] == method:
                call = m[1]
                break

        #Duplicated in the method POST - move to a helper module
        #Parse the args
        keys = request.json.keys()
        req = request.json

        #Setup the python arg / kwarg containers
        args = []
        kwargs = {}
        #Parse the request args and call the method
        if 'args' in keys:
            for a in req['args']:
                try:
                    args.append(ast.literal_eval(a))
                except:
                    args.append(a)
        if 'kwargs' in keys:
            for k, v in req['kwargs'].iteritems():
                try:
                    kwargs[k] =  ast.literal_eval(v)
                except:
                    kwargs[k] = v

        #Check args / kwargs to see if they are python objects
        for i, a in enumerate(args):
            if a in UPLOADED_FILES:
                args[i] = os.path.join(UPLOAD_FOLDER, a)
            #elif a in db.keys():
                #args[i] = db[a]

        for k, v in kwargs.iteritems():
            if v in UPLOADED_FILES:
                kwargs[k] = os.path.join(UPLOAD_FOLDER, v)
            #elif v in db.keys():
                #kwargs[k] = db[k]

        #Make the call and get the return items
        funcreturn = call(*args, **kwargs)

        #Update the database since the object might have been changed
        pObj = cPickle.dumps(obj)
        cur.execute("UPDATE WObj SET Obj=? WHERE Id=?", (sqlite3.Binary(pObj), cachedid))
        get_db().commit()
        cur.close()

        return jsonify(response)

@app.route('/cached/<cachedid>/<attr>/', methods=['GET'])
def get_cached_entry_attr(cachedid, attr):
    """
    Load an object from the DB and return the requested
    attribute as a json object.
    """
    response = {'status':'success','data':{}}

    query = "SELECT Obj FROM WObj WHERE ID = {}".format(cachedid)
    cur = get_db().cursor().execute(query)
    result = cur.fetchone()[0]
    obj = cPickle.loads(str(result))

    #Could be cached - here it is not, we reinspect with each call
    attrs = inspect.getmembers(obj,lambda i : not(inspect.ismethod(i)))
    for a in attrs:
        if a[0] == attr:
            ret = a[1]
            break

    response['data'] = {attr : ret}
    return jsonify(response)

@app.route('/api/<module>/', methods=['GET'])
def get_modules(module):
    methods = funcs[module].keys()
    response = {'status':'success','data':{}}
    response['data']['links'] = []
    for i in methods:
        response['data']['links'].append({'id':'{}'.format(i),
                                          'href':'/api/{}/{}/'.format(module,i)})
    return jsonify(response)

@app.route('/api/<module>/<method>/', methods=['GET'])
def get_method(module, method):
    """
    Query the API to get the POST parameters.
    """
    #Setup the response strings
    response = {'status':'success','data':{}}
    response['data']['post_template'] = {}
    mname = method
    #Extract the method from the method dict
    method = funcs[module][method]

    #Inspect the method to get the arguments
    try:
        reqargs = inspect.getargspec(method)
    except:
        reqargs = inspect.getargspec(method.__init__)

    args = reqargs.args
    defaults = list(reqargs.defaults)
    try:
        args.remove('self')
    except:
        pass

    #Pack the arguments into the pos_template
    response['data']['post_template'] = {'args':[], 'kwargs':{}}
    diff = len(defaults) - len(args)
    for i, arg in enumerate(args):
        if diff < 0:
            diff += 1
            response['data']['post_template']['args'].append(arg)
        else:
            response['data']['post_template']['kwargs'][arg] = defaults[diff]

    response['data']['links'] = {'id':'docs',
                                 'href':'{}/{}/docs/'.format(module, mname)}
    return jsonify(response)

@app.route('/api/<module>/<method>/docs/', methods=['GET'])
def get_docs(module, method):
    """
    Query the API to get the doc string of the method
    """
    response = {'status':'success','data':{}}
    response['data']['docstring'] = []

    #Extract the method from the method dict
    method = funcs[module][method]

    #Introspect the docs
    docs = inspect.getdoc(method)
    for l in docs.split('\n'):
        response['data']['docstring'].append(l)
    return jsonify(response)

@app.route('/api/<module>/<method>/', methods=['POST'])
def post(module,method):
    """
    To make a POST using CURL to the flask dev server:
    Fisher-Jenks using the Hartigan Olympic time example
    curl -i -H "Content-Type: application/json" -X POST -d '{"args":["[12, 10.8, 11, 10.8, 10.8, 10.6, 10.8, 10.3, 10.3,10.3,10.4,10.5,10.2,10.0,9.9]"], "kwargs":{"k":5}}' http://localhost:5000/ap/esda/fisher_jenks/

    or

    Sample Jenks Caspall using the same example - note that sample
     percentage is not passed.
    curl -i -H "Content-Type: application/json" -X POST -d '{"args":["[12, 10.8, 11, 10.8, 10.8, 10.6, 10.8, 10.3, 10.3,10.3,10.4,10.5,10.2,10.0,9.9]"], "kwargs":{"k":5}}'  http://localhost:5000/ai/esda/jenks_caspall_sampled/

    or

    Using the CherryPy server on port 8080
    Queen from shapefile - NOTE: The file must be uploaded already
    curl -i -H "Content-Type: application/json" -X POST -d '{"args":[NAT.shp]}' http://localhost:8080/api/weights/queen_from_shapefile/
    """
    if not request.json:
        response = {'status':'error','data':{}}
        standarderror['data'] = 'Post datatype was not json'
        return jsonify(standarderror), 400
    else:
        response = {'status':'success','data':{}}
        #Setup the call, the args and the kwargs
        call = funcs[module][method]

        #Parse the args
        keys = request.json.keys()
        req = request.json

        #Setup the python arg / kwarg containers
        args = []
        kwargs = {}

        if 'args' in keys:
            for a in req['args']:
                try:
                    args.append(ast.literal_eval(a))
                except:
                    args.append(a)
        if 'kwargs' in keys:
            for k, v in req['kwargs'].iteritems():
                try:
                    kwargs[k] =  ast.literal_eval(v)
                except:
                    kwargs[k] = v

        #Check args / kwargs to see if they are python objects
        for i, a in enumerate(args):
            if a in UPLOADED_FILES:
                args[i] = os.path.join(UPLOAD_FOLDER, a)
            #elif a in db.keys():
                #args[i] = db[a]

        for k, v in kwargs.iteritems():
            if v in UPLOADED_FILES:
                kwargs[k] = os.path.join(UPLOAD_FOLDER, v)
            #elif v in db.keys():
                #kwargs[k] = db[k]

        #Make the call and get the return items
        funcreturn = call(*args, **kwargs)

        #Write the W Object to the database
        if isinstance(funcreturn, ps.W):
            pdata = cPickle.dumps(funcreturn, cPickle.HIGHEST_PROTOCOL)
            cur = get_db().cursor()
            if method == 'queen_from_shapefile':
                m = 'Q'
            else:
                m = 'R'
            obj = (m, sqlite3.Binary(pdata), funcreturn._shpName)
            cur.execute("INSERT INTO WObj values (NULL, ?, ?, ?)",obj)
            get_db().commit()
            cur.close()

            response['data'] = {'Shapefile':funcreturn._shpName,
                                'Weight Type':method}
        else:
            funcreturn = vars(funcreturn)
            for k, v in funcreturn.iteritems():
                if isinstance(v, np.ndarray):
                    funcreturn[k] = v.tolist()

            response['data'] = funcreturn

        return jsonify(response)

#This is not API - can I abstract away and have this in the front-end?
@app.route('/upload/', methods=['POST'])
def upload_file():
    """
    POST - Upload a file to the server (a directory)

    Examples:
    curl -X POST -F filename=@NAT.zip http://localhost:8081/upload/
    curl -X POST -F shp=@columbus.shp -F shx=@columbus.shx -F dbf=@columbus.dbf http:/localhost:8081/upload/
    """
    if request.method == 'POST':
        files = request.files
        uploaded = []
        for f in request.files.itervalues():
            uploaded.append(f)
            #Discard the keys - are they ever important since the user
            # has named the file prior to upload?
            if f and allowed_file(f.filename):
                filename = secure_filename(f.filename)
                savepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                f.save(savepath)
                if filename.split('.')[1] == 'zip':
                    unzip(savepath, app.config['UPLOAD_FOLDER'])

        #Update the file list
        UPLOADED_FILES = update_file_list(UPLOAD_FOLDER)
        #Ideally we store metadata about the upload, but for now just return
        response = {'status':'success','data':{}}
        for u in uploaded:
            response['data'][u.filename] = '{}/{}'.format(app.config['UPLOAD_FOLDER'], u.filename)
        return jsonify(response)

    else:
        response = {'status':'error','data':{'message':'Either "." not in filename or extensions not in approved list.'}}
        return jsonify(response)
    return jsonify(response)


@app.route('/api/', methods=['GET'])
def get_api():
    """
    The api start page.
    """
    response = {'status':'success','data':{}}
    response['data']['links'] = []

    toplevel = funcs.keys()
    for i in toplevel:
        response['data']['links'].append({'id':'{}'.format(i), 'href':'/api/{}'.format( i)})
    return jsonify(response)

@app.route('/listdata/', methods=['GET'])
def get_listdata():
    """
    List the data that is in the upload directory
    """
    response = {'status':'success','data':{}}
    files = {}
    for f in os.listdir(UPLOAD_FOLDER):
        basename = f.split('.')[0]
        if basename not in files.keys():
            files[basename] = []
            files[basename].append(os.path.join(UPLOAD_FOLDER, f))
        else:
            files[basename].append(os.path.join(UPLOAD_FOLDER, f))
    response['data']['files'] = files
    return jsonify(response)

@app.route('/listdata/<filename>/', methods=['GET'])
def get_shpinfo(filename):
    response = {'status':'success','data':{'attributes':{}}}
    #Wrap in a try/except
    files = (os.path.join(UPLOAD_FOLDER, filename))

    #Info about the shapefile
    fhandler = ps.open(files + '.shp', 'r')
    response['data']['geomheader'] = fhandler.header
    fhandler = ps.open(files + '.dbf', 'r')
    response['data']['fields'] = fhandler.header
    response['data']['fields'] += ['thegeom']

    return jsonify(response)


@app.route('/listdata/<filename>/<field>/', methods=['GET'])
def get_shpdbf(filename, field):
    """
    Extract a column from a shapefile (geom) or dbf (attribute)
    """
    files = (os.path.join(UPLOAD_FOLDER, filename))
    if field == 'thegeom':
        geoms = []
        with fiona.collection(files + '.shp', "r") as source:
             for feat in source:
                 geoms.append(feat)

        geojson = {
            "type": "FeatureCollection",
            "features": geoms
            }
        response = {'status':'success','data':{'geojson':geojson}}
    else:
        dbf = ps.open(files + '.dbf', 'r')
        attr = dbf.by_col(field)
        response = {'status':'success','data':{field:attr}}


    return jsonify(response)




@app.teardown_appcontext
def close_connection(exception):
    """
    Gracefully close the DB conncetion
    """
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

if __name__ == '__main__':
    app.config.update(DEBUG=True)
    app.run()
