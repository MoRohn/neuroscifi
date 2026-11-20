"""Independent check of published rounded aggregate choice data. No participant records."""
from math import comb, sqrt
import json


def exact_two_sided(a,b,c,d):
    n=a+b+c+d
    k=a+c
    draw=a+b
    weights=[comb(k,x)*comb(n-k,draw-x) for x in range(max(0,draw-n+k),min(k,draw)+1)]
    observed=comb(k,a)*comb(n-k,draw-a)
    return sum(w for w in weights if w<=observed)/sum(weights)


def wilson(x,n):
    z=1.959963984540054
    p=x/n
    denom=1+z*z/n
    mid=(p+z*z/(2*n))/denom
    half=z*sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    return mid-half,mid+half


def compute(a,b,c,d):
    pa,pb=a/(a+b),c/(c+d)
    la,ua=wilson(a,a+b)
    lb,ub=wilson(c,c+d)
    delta=pa-pb
    return {"counts":[[a,b],[c,d]],"fraction_difference":delta,
            "newcombe_95":[delta-sqrt((pa-la)**2+(ub-pb)**2),
                           delta+sqrt((ua-pa)**2+(pb-lb)**2)],
            "fisher_two_sided":exact_two_sided(a,b,c,d)}


if __name__=="__main__":
    print(json.dumps({"source":"Gweon and Schulz, Science 2011, doi:10.1126/science.1204493",
        "experiment_1":compute(9,5,3,11),
        "experiment_2_pooled_within_agent_conditions":compute(13,6,11,25)},indent=2))
