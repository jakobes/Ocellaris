//lc = 1e-2;
Point(1) = {0, 0, 0, lc};
Point(2) = {2, 0, 0, lc};
Point(3) = {2, 2, 0, lc};
Point(4) = {0, 2, 0, lc};
Line(1) = {1,2};
Line(2) = {2,3};
Line(3) = {3,4};
Line(4) = {4,1};
Line Loop(5) = {4,1,2,3};
Plane Surface(6) = {5};