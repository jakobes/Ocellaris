////////////////////////////////////////////
// Gmsh project created on Wed Nov  1 18:39 2017
SetFactory("OpenCASCADE");


////////////////////////////////////////////
// Parameters

// Parameters of the geometry
DefineConstant[ H = {  30.0, Name "Parameters/Total depth" } ];
DefineConstant[ h = {   4.0, Name "Parameters/Upper layer depth" } ];
DefineConstant[ L = { 200.0, Name "Parameters/Domain length" } ];
DefineConstant[ B = {  30.0, Name "Parameters/Domain breadth" } ];
DefineConstant[ C = {  50.0, Name "Parameters/Hull inlet offset amidships" } ];
// Hull:
DefineConstant[ l = {  30.0, Name "Parameters/Hull length" } ];
DefineConstant[ w = {  11.0, Name "Parameters/Hull width" } ];
DefineConstant[ d = {   5.0, Name "Parameters/Draught amidships" } ];

// Mesh params
DefineConstant[ lc_fine   = { 1.5, Name "Parameters/LC fine" } ];
DefineConstant[ lc_course = { 3.0, Name "Parameters/LC course" } ];


////////////////////////////////////////////
// Geometry

// Mesh control parameter for geometry construction
Q = lc_fine * 0.8;

// Create hull out of ellipses
N = Ceil(d / Q);
dz = d / (N - 0.95);
For i In {0:N+1}
    // Make ellipses
	z = dz - i * dz;
	If (i > 1)
	   z = z + dz * (i / (N + 1))^3;
	EndIf
    radius_x = l / 2 * Sqrt(1 + z / d);
    radius_y = w / 2 * Sqrt(1 + z / d);
    e1 = newl;
    Ellipse(e1) = {C, 0, z, radius_x, radius_y};
    e2 = newl;
    Line Loop(e2) = {e1};
    ellipses[i] = e2;
EndFor
For i In {0:N}
    // Make hull pieces
    surfs = ThruSections{ellipses[i], ellipses[i + 1]};
    top = news; Plane Surface(top) = {ellipses[i]};
    bot = news; Plane Surface(bot) = {ellipses[i + 1]};
    pshell = news;
    Surface Loop(pshell) = {top, surfs[0], bot};
    hull_pieces[i] = newv;
    Volume(hull_pieces[i]) = {pshell};
EndFor

// Create water domain
ocean = newv; Box(ocean) = {0, -B/2, -H,   L, B, H};

For i In {0:N}
	// Delete hull shape from water domain
	ocean_new = newv;
	BooleanDifference(ocean_new) = { Volume{ocean}; Delete; }{ Volume{hull_pieces[i]}; Delete; };
	ocean = ocean_new;
EndFor

// Remove half the domain (negative y-axis part)
half = newv; Box(half) = {0, -B/2, -H,   L, B/2, H};
ocean_new = newv;
BooleanDifference(ocean_new) = { Volume{ocean}; Delete; }{ Volume{half}; Delete; };
ocean = ocean_new;

// Make mesh conform to initial free surface. The free surface mesh conforming
// line is inset 1 mesh cell from the boundary to avoid mesh degeneration there
s1 = news;
Rectangle(s1) = {Q, Q, -h,     L - 2 * Q,  B / 2 - 2 * Q};
If (h < d)
    // Remove ellipse around the hull to avoid surfce/hull intersection
    radius_x = l / 2 * Sqrt(1 - h / (d + 2 * Q));
    radius_y = w / 2 * Sqrt(1 - h / (d + 2 * Q));
    e1 = newl;
    Ellipse(e1) = {C, 0, -h, radius_x, radius_y};
    e2 = newl;
    Line Loop(e2) = {e1};
    s2 = news;
    Plane Surface(s2) = {e2};
    s3 = news;
    BooleanDifference(s3) = { Surface{s1}; Delete; }{ Surface{s2}; Delete; };
Else
    radius_x = Q / 2;
    s3 = s1;
EndIf
Surface {s3} In Volume {ocean}; // Conform to this plane surface

// Mesh conforming line in the inlet plane
p1 = newp; Point(p1) = {0,   0 + Q, -h};
p2 = newp; Point(p2) = {0, B/2 - Q, -h};
l1 = newl; Line(l1) = {p1, p2};
Line{l1} In Surface{1}; // FIXME: surface number extracted from GUI

// Mesh conforming line in the outlet plane
p1 = newp; Point(p1) = {L,   0 + Q, -h};
p2 = newp; Point(p2) = {L, B/2 - Q, -h};
l1 = newl; Line(l1) = {p1, p2};
Line{l1} In Surface{6}; // FIXME: surface number extracted from GUI

// Mesh conforming lines in the center plane
p1 = newp; Point(p1) = {0 + Q, 0, -h};
p2 = newp; Point(p2) = {C - radius_x, 0, -h};
p3 = newp; Point(p3) = {C + radius_x, 0, -h};
p4 = newp; Point(p4) = {L - Q, 0, -h};
l1 = newl; Line(l1) = {p1, p2};
l2 = newl; Line(l2) = {p3, p4};
Line{l1} In Surface{2}; // FIXME: surface number extracted from GUI
Line{l2} In Surface{2}; // FIXME: surface number extracted from GUI

// Mesh conforming line in the off-center plane
p1 = newp; Point(p1) = {0 + Q, B/2, -h};
p2 = newp; Point(p2) = {L - Q, B/2, -h};
l1 = newl; Line(l1) = {p1, p2};
Line{l1} In Surface{4}; // FIXME: surface number extracted from GUI


////////////////////////////////////////////
// Mesh cell size fields:

// Approximate radius of hull
r = l^2 / (8 * d) + d / 2;

// Fine mesh near the hull
Field[1] = Ball;
Field[1].Radius = r + 3;
Field[1].XCenter = C;
Field[1].YCenter = 0;
Field[1].ZCenter = r - d;
Field[1].VIn = lc_fine;
Field[1].VOut = lc_course;

// Fine mesh near the pycnocline
Field[2] = Box;
Field[2].XMin = 0;
Field[2].XMax = L;
Field[2].YMin = -B;
Field[2].YMax = B;
Field[2].ZMin = -h - 4;
Field[2].ZMax = -h + 4;
Field[2].VIn = lc_fine;
Field[2].VOut = lc_course;

// Fine mesh in the immediate wake of the hull
Field[3] = Box;
Field[3].XMin = C;
Field[3].XMax = C + l * 2;
Field[3].YMin = -w;
Field[3].YMax =  w;
Field[3].ZMin = -h - 4;
Field[3].ZMax =  0;
Field[3].VIn = lc_fine;
Field[3].VOut = lc_course;

// Fine mesh near the inlet
Field[4] = Box;
Field[4].XMin = 0;
Field[4].XMax = C / 5;
Field[4].YMin = -B;
Field[4].YMax =  B;
Field[4].ZMin = -H;
Field[4].ZMax =  0;
Field[4].VIn = lc_fine;
Field[4].VOut = lc_course;

// Fine mesh near the surface at the outlet
Field[5] = Box;
Field[5].XMin = L - C / 10;
Field[5].XMax = L;
Field[5].YMin = -B;
Field[5].YMax =  B;
Field[5].ZMin = -h - 2;
Field[5].ZMax =  0;
Field[5].VIn = lc_fine;
Field[5].VOut = lc_course;

// The resulting mesh size field is the minimum of the above fields
Field[100] = Min;
Field[100].FieldsList = {1, 2, 3, 4, 5};
Background Field = 100;


////////////////////////////////////////////
// Physical domains

Physical Volume(100) = { ocean }; // The fluid domain


// The end, code below is added by gmsh GUI
////////////////////////////////////////////
