import sys, numpy as np, collections
import ssdata as D, ssalgos as A
trials = D.load_trials(sys.argv[1])
rules = A.Rules(); naive = A.ThresholdOnly(2.5)
agree=collections.defaultdict(lambda:[0,0]); nag=collections.defaultdict(lambda:[0,0])
worst=[]
for t in trials:
    c = D.run_method(rules,t); nv = D.run_method(naive,t)
    m = (t.ms >= t.ms[0] + 4000)[:,None] & np.ones_like(t.t_log,bool)   # skip first 4 s (board carried state from previous trial)
    a = (c==t.t_log)[m]; agree[t.name][0]+=a.sum(); agree[t.name][1]+=a.size
    b = (nv==t.n_log)[m]; nag[t.name][0]+=b.sum(); nag[t.name][1]+=b.size
    worst.append((a.mean(), t.id))
print("%-12s %18s %18s"%("scenario","rules vs logged t","naive vs logged n"))
for s in sorted(agree): print("%-12s %17.1f%% %17.1f%%"%(s,100*agree[s][0]/agree[s][1],100*nag[s][0]/nag[s][1]))
tot=sum(v[0] for v in agree.values())/sum(v[1] for v in agree.values()); print("overall rules agreement %.2f%%"%(100*tot))
print("worst trials:",sorted(worst)[:6])
