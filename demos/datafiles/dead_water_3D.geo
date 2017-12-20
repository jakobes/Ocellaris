// Gmsh project created on Wed Nov  1 18:39 2017
SetFactory("OpenCASCADE");

////////////////////////////////////////////
// Parameters

// Parameters of the geometry
DefineConstant[ H = {  30.0, Name "Parameters/Total depth" } ];
DefineConstant[ h = {   6.0, Name "Parameters/Upper layer depth" } ];
DefineConstant[ L = { 200.0, Name "Parameters/Domain length" } ];
DefineConstant[ B = {  30.0, Name "Parameters/Domain breadth" } ];
DefineConstant[ C = {  50.0, Name "Parameters/Hull inlet offset amidships" } ];
// Hull:
DefineConstant[ l = {  30.0, Name "Parameters/Hull length" } ];
DefineConstant[ w = {  11.0, Name "Parameters/Hull width" } ];
DefineConstant[ d = {   5.0, Name "Parameters/Draught amidships" } ];

// Mesh params
DefineConstant[ lc_fine   = { 2.0, Name "Parameters/LC fine" } ];
DefineConstant[ lc_course = { 5.0, Name "Parameters/LC course" } ];

////////////////////////////////////////////
// Geometry

Macro ComputePosition
	// Compute oordinates of position (ip, jp) 
	x = l*ip/(N - 1) - l/2;
	y = w*jp/(M - 1) - w/2;
	z = -d*(1 - (2*x/l)^2 - (2*y/w)^2);
Return

Macro MakePointIfNotExists
	// This macro takes parameters ip and jp
	// and creates a point in x_ip, y_jp
	If ( ! Exists( mypoint~{ip}~{jp} ) ) 
		Call ComputePosition;
		mypoint~{ip}~{jp} = newp;
		Point(mypoint~{ip}~{jp}) = {C + x, y, z};
	EndIf
Return

Macro MakeLineIfNotExists 
   // This macro takes parameters i0, i1, j0, j1
   // and defines a line from (i0, j0) to (i1, j1)
	If ( ! Exists( myline~{i0}~{j0}~{i1}~{j1} ) ) 
		myline~{i0}~{j0}~{i1}~{j1} = newl;
		Line(myline~{i0}~{j0}~{i1}~{j1}) = {mypoint~{i0}~{j0}, mypoint~{i1}~{j1}};
	EndIf
Return

// Create hull out of multiple pieces
N = 11;
M = 11;
ipiece = 0;
For i In {0:N-2}
	For j In {0:M-2}
		// Make points
		ip = i + 0; jp = j + 0; Call MakePointIfNotExists;
		ip = i + 1; jp = j + 0; Call MakePointIfNotExists;
		ip = i + 1; jp = j + 1; Call MakePointIfNotExists;
		ip = i + 0; jp = j + 1; Call MakePointIfNotExists;
		// Make lines
		i0 = i + 0; j0 = j + 0; i1 = i + 1; j1 = j + 0; Call MakeLineIfNotExists;
		i0 = i + 1; j0 = j + 0; i1 = i + 1; j1 = j + 1; Call MakeLineIfNotExists;
		i0 = i + 1; j0 = j + 1; i1 = i + 0; j1 = j + 1; Call MakeLineIfNotExists;
		i0 = i + 0; j0 = j + 1; i1 = i + 0; j1 = j + 0; Call MakeLineIfNotExists;
		// Make line loop
		myloop~{i}~{j} = newl;
		Line Loop(myloop~{i}~{j}) = { myline~{i + 0}~{j + 0}~{i + 1}~{j + 0},
                                      myline~{i + 1}~{j + 0}~{i + 1}~{j + 1},
                                      myline~{i + 1}~{j + 1}~{i + 0}~{j + 1},
                                      myline~{i + 0}~{j + 1}~{i + 0}~{j + 0} };
		mysurf~{i}~{j} = news;
		Plane Surface ( mysurf~{i}~{j} ) = { myloop~{i}~{j} };
		
		// Extrude the surface piece upwards
		out[] = Extrude{0, 0, 2*d }{ Surface{mysurf~{i}~{j}}; };
		ipiece = ipiece + 1;
		hull_pieces[ipiece] = out[1];
	EndFor
EndFor

// Create water domain
ocean = newv; Box(ocean) = {0, -B/2, -H,   L, B, H};

For k In {1:ipiece}
	// Delete hull shape from water domain
	ocean_new = newv;
	BooleanDifference(ocean_new) = { Volume{ocean}; Delete; }{ Volume{hull_pieces[k]}; Delete; };
	ocean = ocean_new;
EndFor


// Make mesh conform to initial free surface. The free surface mesh conforming
// line is inset 1 mesh cell from the boundary to avoid mesh degeneration there
s = news; Rectangle(s) = {lc_fine, -B/2 + lc_fine, -h,     L - 2 * lc_fine,  B - 2 * lc_fine};
Surface {s} In Volume {ocean}; // Conform to this plane surface


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
