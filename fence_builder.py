"""

"""

import osmium as o
import numpy as np
import os
import time
import math

#import cProfile, pstats, io
#pr = cProfile.Profile()
#pr.enable()

class fence_struct:
    def __init__(self, tag, tags):
        self.tag = tag
        self.name = None
        if (tags != None) and ('name' in tags):
            self.name = tags['name']
        self.ways = []
        self.area = None

class way_struct:
    def __init__(self, ref, outer):
        self.ref = ref
        self.outer = outer

class node_struct:
    def __init__(self, lat, lon, len):
        self.lat = lat
        self.lon = lon
        self.len = len

def check_tags(test_tags, desired_tags):
    for tag in desired_tags:
        if tag[0] in test_tags and test_tags[tag[0]] == tag[1]:
            return_tag = tag[1]
            if (tag[2] != None) and (tag[1] in test_tags):
                if test_tags[tag[1]] not in tag[2]:
                    break
                return_tag = test_tags[tag[1]]
            # found tag
            return return_tag
    return None

def debug_print(string):
    if False:
        print(string)

# find all ways or multipolygons with the given tags
class fence_search(o.SimpleHandler):

    def __init__(self, fences, tags):
        super(fence_search, self).__init__()
        self.fences = fences
        self.tags = tags

    def way(self, w):
        found_tag = check_tags(w.tags, self.tags)
        if found_tag != None:
            new_fence = fence_struct(found_tag, w.tags)
            new_fence.ways.append(way_struct(w.id, True))
            self.fences.append(new_fence)

    def relation(self, r):
        if ('type' not in r.tags) or (r.tags['type'] != 'multipolygon'):
            return

        found_tag = check_tags(r.tags, self.tags)
        if found_tag != None:
            new_fence = fence_struct(found_tag, r.tags)
            for m in r.members:
                if m.type == 'w':
                    new_fence.ways.append(way_struct(m.ref, m.role == 'outer'))
            self.fences.append(new_fence)

# find the nodes in given ways
class way_search(o.SimpleHandler):

    def __init__(self, ways):
        super(way_search, self).__init__()
        self.ways = ways

    def way(self, w):
        if w.id in self.ways:
            num_nodes = len(w.nodes)
            lat = np.empty(num_nodes)
            lon = np.empty(num_nodes)
            for i in range(num_nodes):
                if w.nodes[i].location.valid():
                    lat[i] = w.nodes[i].location.lat
                    lon[i] = w.nodes[i].location.lon
                else:
                    lat[i] = np.NaN
                    lon[i] = np.NaN
            self.ways[w.id] = node_struct(lat, lon, num_nodes)

def combine_ways(ways, way_dict):
    num_ways = len(ways)
    for i in range(num_ways):
        for j in range(i+1, num_ways):
            if ways[i].outer != ways[j].outer:
                # must be of same type to combine
                continue
            # get nodes for first  and second way
            nodes_a = way_dict[ways[i].ref]
            nodes_b = way_dict[ways[j].ref]

            if (nodes_a.lat[-1] == nodes_b.lat[0]) and (nodes_a.lon[-1] ==nodes_b.lon[0]):
                debug_print('ways %i, %i share first point' % (i,j))
                new_lat = np.concatenate([nodes_a.lat,nodes_b.lat[1:]])
                new_lon = np.concatenate([nodes_a.lon,nodes_b.lon[1:]])

            elif (nodes_a.lat[-1] == nodes_b.lat[-1]) and (nodes_a.lon[-1] ==nodes_b.lon[-1]):
                debug_print('ways %i, %i share last point' % (i,j))
                new_lat = np.concatenate([nodes_a.lat,np.flip(nodes_b.lat[0:-1])])
                new_lon = np.concatenate([nodes_a.lon,np.flip(nodes_b.lon[0:-1])])

            else:
                continue

            # make sure first and last point are not the same
            new_length = nodes_a.len + nodes_b.len - 1
            if (new_lat[0] ==  new_lat[-1]) and (new_lon[0] ==  new_lon[-1]):
                new_length -= 1

            # create new nodes and add to way dictionary, use negative keys to avoid conflict
            key = min(min(list(way_dict.keys())),0) -1
            way_dict[key] = node_struct(new_lat[0:new_length], new_lon[0:new_length], new_length)

            # point first way to new key
            ways[i].ref = key

            # remvoe second way
            del ways[j]

            # recursion!
            return combine_ways(ways, way_dict)
    return ways

def remove_ways_and_fences(fences, way_dict):
    # remove fence ways that are no longer in the dictionary
    for i, fence in enumerate(fences):
        way_valid = len(fence.ways)*[True]
        for j, way in enumerate(fence.ways):
            if way.ref not in way_dict:
                way_valid[j] = False
        if False in way_valid:
            fence.ways = [item for i, item in enumerate(fence.ways) if way_valid[i]]

    # remove fences with no ways
    fence_valid = len(fences)*[False]
    for i, fence in enumerate(fences):
        if len(fence.ways) == 0:
            continue
        for way in fence.ways:
            if way.outer:
                fence_valid[i] = True
                break
    if False in fence_valid:
        fences = [item for i, item in enumerate(fences) if fence_valid[i]]
    return fences

def wrap_180(diff):
    if diff > 180:
        wrap_180(diff - 360)
    elif diff < -180:
        wrap_180(diff + 360)
    return diff

def longitude_scale(lat):
    scale = math.cos(math.radians(lat))
    return max(scale, 0.01)

# convert to xy relative to origin point
LATLON_TO_M = 6378100 * (math.pi / 180)
def convert_to_cartesian(lat, lon, origin_lat, origin_lon):
    num_nodes = len(lat)
    x = np.empty(num_nodes)
    y = np.empty(num_nodes)
    for i in range(num_nodes):
        x[i] = (lat[i]-origin_lat) * LATLON_TO_M
        y[i] = wrap_180(lon[i] - origin_lon) * LATLON_TO_M * longitude_scale((lat[i]+origin_lat)*0.5)
    return x, y

def polygon_intersects(x, y):
    num_nodes = len(x)
    # compare each line with all others
    for i in range(num_nodes):
        j = i+1
        if j >= num_nodes:
            j = 0

        # no point in testing adjacent segments start at i + 2
        for k in  range(i+2,num_nodes):
            if (i == 0) and (k == num_nodes-1):
                # first and last lines are adjacent
                continue

            l = k+1
            if l == num_nodes:
                l = 0

            if line_intersects((x[i], y[i]), (x[j], y[j]), (x[k], y[k]), (x[l], y[l])):
            #if line_intersects_fast((x[i], y[i]), (x[j], y[j]), (x[k], y[k]), (x[l], y[l])):
                return True
    return False

def line_intersects(seg1_start, seg1_end, seg2_start, seg2_end):
    # implementation borrowed from http://stackoverflow.com/questions/563198/how-do-you-detect-where-two-line-segments-intersect
    r1 = (seg1_end[0] - seg1_start[0], seg1_end[1] - seg1_start[1])
    r2 = (seg2_end[0] - seg2_start[0], seg2_end[1] - seg2_start[1])
    r1xr2 = r1[0]*r2[1] - r1[1]*r2[0]
    if abs(r1xr2) < 1e-09:
        # either collinear or parallel and non-intersecting
        return False
    else:
        ss2_ss1 = (seg2_start[0] - seg1_start[0], seg2_start[1] - seg1_start[1])
        q_pxr = ss2_ss1[0]*r1[1] - ss2_ss1[1]*r1[0]
        # t = (q - p) * s / (r * s)
        # u = (q - p) * r / (r * s)
        t = (ss2_ss1[0]*r2[1] - ss2_ss1[1]*r2[0]) / r1xr2
        u = q_pxr / r1xr2
        if (u >= 0) and (u <= 1) and (t >= 0) and (t <= 1):
            # lines intersect
            # t can be any non-negative value because (p, p + r) is a ray
            # u must be between 0 and 1 because (q, q + s) is a line segment
            #intersection = seg1_start + (r1*t);
            return True
        else:
            # non-parallel and non-intersecting
            return False

def ccw(A,B,C):
    return (C[1]-A[1])*(B[0]-A[0]) > (B[1]-A[1])*(C[0]-A[0])

def line_intersects_fast(A, B, C, D):
    return ccw(A,C,D) != ccw(B,C,D) and ccw(A,B,C) != ccw(A,B,D)

# https://github.com/rowanwins/sweepline-intersections
def polygon_intersects_sweep(x, y):
    # list of lines in polygon
    num_nodes = len(x)

    lines = {}
    event_que = 2*num_nodes*[None]
    for i in range(num_nodes):
        j = i+1
        if j >= num_nodes:
            j = 0
        lines[i] = ((x[i],y[i]), (x[j], y[j]))
        if x[i] <= x[j]:
            event_que[i*2] = (x[i],i,True)
            event_que[(i*2)+1] = (x[j],i,False)
        else:
            event_que[i*2] = (x[i],i,False)
            event_que[(i*2)+1] = (x[j],i,True)

    event_que = sorted(event_que, key=lambda event: event[0])
    active = {}
    for event in event_que:
        if event[2]:
            # adding new line, intersect with active items
            new_line = lines[event[1]]

            # don't compare adjacent lines in polygon
            next_line = event[1] + 1
            if next_line == num_nodes:
                next_line = 0
            prev_line = event[1] - 1
            if prev_line == -1:
                prev_line = num_nodes - 1

            for key, line in active.items():
                if (key == next_line) or (key == prev_line):
                    continue
                if line_intersects(new_line[0], new_line[1], line[0], line[1]):
                    return True
            active[event[1]] = new_line

        else:
            # remove line from active list
            active.pop(event[1])

    return False

# Check for intersections between polygons
def polygon_polygon_intersection(x, y):
    num_poly = len(x)

    lines = {}
    event_que = []
    key = 0
    for k in range(num_poly):
        num_nodes = len(x[k])
        for i in range(num_nodes):
            j = i+1
            if j >= num_nodes:
                j = 0
            lines[key] = ((x[k][i], y[k][i]), (x[k][j], y[k][j]), k)
            right_to_left = x[k][i] <= x[k][j]
            event_que.append((x[k][i], key, right_to_left))
            event_que.append((x[k][j], key, not right_to_left))
            key += 1

    event_que = sorted(event_que, key=lambda event: event[0])
    active = {}
    for event in event_que:
        if event[2]:
            # adding new line, intersect with active items
            new_line = lines[event[1]]

            for line in active.values():
                if new_line[2] == line[2]:
                    # don't compare lines in same polygon
                    continue
                if line_intersects(new_line[0], new_line[1], line[0], line[1]):
                    return True
            active[event[1]] = new_line

        else:
            # remove line from active list
            active.pop(event[1])

    return False

# https://en.wikipedia.org/wiki/Shoelace_formula
def polygon_area(x, y):
    sum1 = x[-1] * y[0]
    sum2 = x[0] * y[-1]
    for i in range(len(x)-1):
        sum1 += x[i] * y[i+1]
        sum2 += y[i] * x[i+1]
    return abs(sum1 - sum2) * 0.5

def point_outside_polygon(point_x, point_y, poly_x, poly_y):
    # step through each edge pair-wise looking for crossings:
    num_nodes = len(poly_x)
    outside = True
    min_x = min(poly_x) - 1

    for i in range(num_nodes):
        j = i+1
        if j >= num_nodes:
            j = 0
        #if (poly_y[i] > point_y) == (poly_y[j] > point_y):
            # both ends of line are on the same side of the point
            # no intersection possible
            #continue
        if line_intersects((min_x, point_y), (point_x, point_y) , (poly_x[i], poly_y[i]), (poly_x[j], poly_y[j])):
            outside = not outside

    return outside

start_time = time.time()
step_time = start_time

input_file = 'map.osm'
input_file = 'bigger_map.osm'
input_file = 'switzerland-padded.osm.pbf'
#input_file = 'switzerland-small-map.osm'
#input_file = 'island.osm'
#input_file ='Heidsee.osm'

# output directory
directory = 'Fences'

# search tags
tags = (('landuse', 'reservoir', None),
        ('natural', 'water', ('lake', 'reservoir', 'basin', 'lagoon', 'pond')))

# Only output fences with outer area larger than this
area_threshold = 1000 # m^2

# search tags
#tags = [['landuse', 'reservoir', None]]

# find all ways with given tags
fences = []
fence_search(fences, tags).apply_file(input_file)

if len(fences) == 0:
    raise Exception('Could not find any fences')

print("found %i fences in %0.2fs" % (len(fences), time.time() - step_time))
step_time = time.time()

# create list of all ways
ways = []
for fence in fences:
    for way in fence.ways:
        ways.append(way.ref)

# dictionary of ways
way_dict = dict.fromkeys(ways)

# find locations associated with each way
way_search(way_dict).apply_file(input_file, locations=True)

print("got matching ways in %0.2fs" % (time.time() - step_time))
step_time = time.time()

# remove bad locations and bad ways from dictionary
for key, value in way_dict.items():
    if value == None:
        # way not found
        debug_print('way %i no points' % (key))
        continue
    if not np.isnan(value.lat).any():
        continue
    # remove nan values
    value.lat = value.lat[~np.isnan(value.lat)]
    value.lon = value.lon[~np.isnan(value.lon)]
    new_len = len(value.lat)
    if len(value.lon) != new_len:
        raise Exception('nubmer of lat and lon point not equal')
    if new_len > 1:
        debug_print('removed %i bad values from way %i' % (value.len - new_len, key))
        value.len = new_len
    else:
        value = None
        debug_print('removed way %i' % (key))
    way_dict[key] = value


# remove ways with no points
filtered = {key: value for key, value in way_dict.items() if value is not None}
way_dict.clear()
way_dict.update(filtered)

# remove fence ways that are no longer in the dictionary
fences = remove_ways_and_fences(fences, way_dict)

print("removed bad locations in %0.2fs" % (time.time() - step_time))
step_time = time.time()

# combine ways that share points
for i in range(len(fences)):
    fences[i].ways = combine_ways(fences[i].ways, way_dict)

# make sure ways do not have same start and end points
for key, removed in way_dict.items():
    if (removed.lat[0] ==  removed.lat[-1]) and (removed.lon[0] ==  removed.lon[-1]):
        way_dict[key] = node_struct(removed.lat[0:-1], removed.lon[0:-1], removed.len -1)

# remove ways with fewer than 3 points
filtered = {key: value for key, value in way_dict.items() if value.len >= 3}
way_dict.clear()
way_dict.update(filtered)

# remove fence ways that are no longer in the dictionary
fences = remove_ways_and_fences(fences, way_dict)

print("combined ways in %0.2fs" % (time.time() - step_time))
step_time = time.time()

# check users of each way
way_count = dict.fromkeys(way_dict.keys(), 0)
for fence in fences:
    for way in fence.ways:
        way_count[way.ref] += 1
# copy duplicated ways to new key
for i, fence in enumerate(fences):
    for j, way in enumerate(fence.ways):
        if way_count[way.ref] > 1:
            key = min(list(way_dict.keys())) - 1
            way_dict[key] = way_dict[way.ref]
            way_count[key] = 1
            way_count[way.ref] -= 1
# remove unused items
if max(way_count.values()) > 1:
    raise Exception('duplicate ways')
filtered = {key: value for key, value in way_dict.items() if way_count[key] == 1}
way_dict.clear()
way_dict.update(filtered)

# make sure polygon's are not self intersecting
for key, nodes in way_dict.items():
    if nodes.len == 3:
        # intersection not possible
        continue
    x, y = convert_to_cartesian(nodes.lat, nodes.lon, nodes.lat[0], nodes.lon[0])
    if polygon_intersects_sweep(x, y):
        way_dict[key] = None
filtered = {key: value for key, value in way_dict.items() if value != None}
way_dict.clear()
way_dict.update(filtered)

fences = remove_ways_and_fences(fences, way_dict)

print("validated polygons in %0.2fs" % (time.time() - step_time))
step_time = time.time()

# move each outer way to own fence
new_fences = []
for i, fence in enumerate(fences):
    outer_fence = len(fence.ways)*[False]
    for j, way in enumerate(fence.ways):
        outer_fence[j] = way.outer
    outer_count = outer_fence.count(True)
    if outer_count == 1:
        continue
    outer_count = 0
    for j, way in enumerate(fence.ways):
        if way.outer:
            outer_count += 1
            if outer_count == 1:
                # first way stays in original fence
                outer_fence[j] = False
                continue
            new_fence = fence_struct(fence.tag, None)
            if fence.name:
                new_fence.name = ('%s - %i') % (fence.name, outer_count)
            new_fence.ways.append(way)
            for inner in fence.ways:
                if inner.outer:
                    continue
                # add all inner zones to new fence
                new_fence.ways.append(inner)
            new_fences.append(new_fence)
    # remove extra outers from original fence
    fences[i].ways = [item for j, item in enumerate(fence.ways) if not outer_fence[j]]
    if fence.name:
        fence.name = ('%s - %i') % (fence.name, 1)
fences.extend(new_fences)

# remove fences with area less than threshold
fence_valid = len(fences)*[False]
for i, fence in enumerate(fences):
    for way in fence.ways:
        if way.outer:
            nodes = way_dict[way.ref]
            x, y = convert_to_cartesian(nodes.lat, nodes.lon, nodes.lat[0], nodes.lon[0])
            fences[i].area = polygon_area(x, y)
            if fences[i].area > area_threshold:
                fence_valid[i] = True
            break
if False in fence_valid:
    fences = [item for i, item in enumerate(fences) if fence_valid[i]]

print("filtered by area in %0.2fs" % (time.time() - step_time))
step_time = time.time()

# check inner polygons points are within outer
for i, fence in enumerate(fences):
    if len(fence.ways) == 1:
        # single way must be a outer
        continue
    for way in fence.ways:
        if way.outer:
            nodes = way_dict[way.ref]
            x, y = convert_to_cartesian(nodes.lat, nodes.lon, nodes.lat[0], nodes.lon[0])

    way_valid = len(fence.ways)*[True]
    for j, way in enumerate(fence.ways):
        if way.outer:
            continue
        inner_nodes = way_dict[way.ref]
        inner_x, inner_y = convert_to_cartesian(inner_nodes.lat, inner_nodes.lon, nodes.lat[0], nodes.lon[0])
        if polygon_polygon_intersection([x,inner_x], [y,inner_y]):
            raise Exception('polygon polygon intersection')
        # no polygon intersections, only need to check a single point
        way_valid[j] = not point_outside_polygon(inner_x[0], inner_y[0], x, y)
    if fences[i] and False in way_valid:
        fences[i].ways = [item for i, item in enumerate(fence.ways) if way_valid[i]]

print("Pruned exclusions in %0.2fs" % (time.time() - step_time))
step_time = time.time()




# TODO:
# make sure inners are within outer
# simplify

# make directory is not present
if not os.path.exists(directory):
    os.mkdir(directory)

# delete existing fences in directory
files = os.listdir(directory)
for inners in files:
    if inners.endswith(".waypoints"):
        os.remove(os.path.join(directory, inners))

# go through each fence and write to file
export_count = 0
for fence in fences:
    if fence == None:
        continue
    name = 'unnamed'
    if fence.name:
        name = fence.name

    # make sure there are no slashes
    name = name.replace('/', '_')
    name = name.replace('\\', '_')

    num_nodes = 0
    num_ways = len(fence.ways)
    centroid_lat = np.empty(num_ways)
    centroid_lon = np.empty(num_ways)
    has_valid_ways = False
    for i, way in enumerate(fence.ways):
        removed = way_dict[way.ref]
        centroid_lat[i] = np.mean(removed.lat)
        centroid_lon[i] = np.mean(removed.lon)
        num_nodes += removed.len
        has_valid_ways |= way.outer

    f = open(os.path.join(directory, '%s - %s - %0.0f m^2 - %i nodes %f %f.waypoints' % (name, fence.tag, fence.area, num_nodes, np.mean(centroid_lat), np.mean(centroid_lon))), "w") 
    f.write('QGC WPL 110\n')
    total_points = 1
    for way in fence.ways:
        removed = way_dict[way.ref]
        wp_type = 5001
        if not way.outer:
            wp_type = 5002
        for i in range(removed.len):
            f.write('%i 0 3 %i %i 0 0 0 %f %f %i 1\n' % (total_points, wp_type, removed.len, removed.lat[i], removed.lon[i], i))
            total_points += 1
    f.close()
    export_count += 1

print("generated %i fences in %0.2fs" % (export_count, time.time() - step_time))
print("Took %0.2fs" % (time.time() - start_time))

#pr.disable()
#s = io.StringIO()
#sortby = 'cumulative'
#ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
#ps.print_stats()
#print(s.getvalue())