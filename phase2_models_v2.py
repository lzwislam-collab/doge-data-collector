"""Official sklearn training, inert JSON inference, disjoint sigmoid calibration."""
import math
import numpy as np
import phase1_research as prior
import phase2_research as v1

def fit_base(setting,x,y,p):
 x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float)
 if x.ndim!=2 or not len(x) or len(x)!=len(y) or not np.isfinite(x).all() or not np.isin(y,[0,1]).all():raise ValueError('invalid training')
 if setting['model']!='gradient_boosting':return {'kind':'linear','model':v1.fit(setting,x,y,p),'n_train':len(x)}
 import sklearn
 if sklearn.__version__!='1.7.2':raise ValueError('pinned sklearn required')
 from sklearn.ensemble import GradientBoostingClassifier
 if len(set(y))==1:return {'kind':'constant','p':round((1+int(y.sum()))/(2+len(y)),12),'n_train':len(y)}
 estimator=GradientBoostingClassifier(n_estimators=p['tree_estimators'],max_depth=setting['depth'],
  min_samples_leaf=p['tree_min_leaf'],learning_rate=.05,subsample=1,random_state=p['seed'],n_iter_no_change=None).fit(x,y)
 trees=[]
 for member in estimator.estimators_[:,0]:
  t=member.tree_;trees.append({'left':t.children_left.tolist(),'right':t.children_right.tolist(),'feature':t.feature.tolist(),
    'threshold_hex':[float(q).hex() for q in t.threshold],'value_hex':[float(q).hex() for q in t.value[:,0,0]]})
 prior_up=float(estimator.init_.class_prior_[1]);initial=math.log(prior_up/(1-prior_up))
 model={'kind':'gradient_boosting','trees':trees,'initial_logit_hex':initial.hex(),'learning_rate_hex':float(.05).hex(),
        'n_train':len(y),'feature_count':x.shape[1],'sklearn_version':'1.7.2','numeric_contract':'exact_hex_tree_parameters_float32_inputs_12place_probabilities'}
 actual=predict_base(model,x);expected=estimator.predict_proba(x)[:,1]
 if not np.allclose(actual,expected,atol=2e-12,rtol=0):raise ValueError('official tree export replay differs')
 return model

def predict_base(model,x):
 if model['kind']=='linear':return v1.predict(model['model'],x)
 if model['kind']=='constant':return [model['p']]*len(x)
 if model['kind']!='gradient_boosting':raise ValueError('unsupported JSON model')
 x=np.asarray(x,dtype=np.float32)
 if x.ndim!=2 or x.shape[1]!=model['feature_count'] or not np.isfinite(x).all():raise ValueError('invalid inference features')
 score=np.full(len(x),float.fromhex(model['initial_logit_hex']));lr=float.fromhex(model['learning_rate_hex'])
 for tree in model['trees']:
  stack=[(0,np.arange(len(x)))];values=np.empty(len(x))
  while stack:
   node,indices=stack.pop()
   if not len(indices):continue
   if tree['left'][node]==-1:values[indices]=float.fromhex(tree['value_hex'][node]);continue
   left=x[indices,tree['feature'][node]].astype(np.float64)<=float.fromhex(tree['threshold_hex'][node])
   stack.extend([(tree['left'][node],indices[left]),(tree['right'][node],indices[~left])])
  score+=lr*values
 return prior.canonical((1/(1+np.exp(-np.clip(score,-30,30)))).tolist())

def logits(probs):
 q=np.clip(np.asarray(probs,dtype=float),1e-6,1-1e-6)
 return [[float(v)] for v in np.log(q/(1-q))]

def calibrate(base,x,y,p):
 cal=prior.fit(logits(predict_base(base,x)),y,{**p,'l2':1.0})
 return {'base':base,'calibration':'sigmoid','calibrator':cal,'n_calibration':len(y),
         'probability_calibration_validated':False}

def predict(model,x):
 q=predict_base(model['base'],x)
 return q if model['calibration']=='raw' else prior.predict(model['calibrator'],logits(q))
