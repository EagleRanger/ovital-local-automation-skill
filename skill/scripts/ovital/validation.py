"""Finite numeric gates shared by public parameter and ABI boundaries."""
import math
from numbers import Real

def finite_number(value,name, *, minimum=None,maximum=None,positive=False,integer=False):
    if isinstance(value,bool) or not isinstance(value,Real):raise ValueError(name+' must be a real numeric value, not bool/text')
    try:number=float(value)
    except (OverflowError,TypeError,ValueError):raise ValueError(name+' must be finite') from None
    if not math.isfinite(number):raise ValueError(name+' must be finite')
    if integer and int(value)!=value:raise ValueError(name+' must be an integer')
    if positive and number<=0:raise ValueError(name+' must be positive')
    if minimum is not None and value<minimum:raise ValueError(name+' is below its allowed range')
    if maximum is not None and value>maximum:raise ValueError(name+' is above its allowed range')
    return int(value) if integer else number

def finite_bounds(bounds, *, geographic=False):
    values=tuple(bounds)
    if len(values)!=4:raise ValueError('bounds must contain west,south,east,north')
    west,south,east,north=[finite_number(value,'bounds coordinate') for value in values]
    if not west<east or not south<north:raise ValueError('positive ordered extent required')
    # Also catch overflow in a difference between two otherwise finite values.
    finite_number(east-west,'bounds width',positive=True);finite_number(north-south,'bounds height',positive=True)
    if geographic and not (-180<=west<east<=180 and -90<=south<north<=90):raise ValueError('geographic extent outside longitude/latitude range')
    return west,south,east,north
