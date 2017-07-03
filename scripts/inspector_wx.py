"""
Inspect timestep reports from one or more Ocellaris restart files 
"""
import os
import h5py
import numpy
import wx
from wx.lib.embeddedimage import PyEmbeddedImage
import matplotlib
matplotlib.use('WxAgg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas, NavigationToolbar2WxAgg as NavigationToolbar


def read_reports(file_name, derived=True):
    if file_name.endswith('h5'):
        return read_reports_h5(file_name, derived)
    else:
        return read_reports_log(file_name, derived)


def read_reports_h5(h5_file_name, derived=True):
    hdf = h5py.File(h5_file_name, 'r')
    
    reps = {}
    for rep_name in hdf['/reports']:
        reps[rep_name] = numpy.array(hdf['/reports'][rep_name])
    
    if derived:
        if 'Ep' in reps and 'Ek' in reps and 'Et' not in reps:
            reps['Et'] = reps['Ek'] + reps['Ep']  
    
    return reps


def read_reports_log(log_file_name, derived=True):
    data = {}
    for line in open(log_file_name, 'rt'):
        if line.startswith('Reports for timestep'):
            parts = line[12:].split(',')
            for pair in parts:
                try:
                    key, value = pair.split('=')
                    key = key.strip()
                    value = float(value)
                    data.setdefault(key, []).append(value)
                except:
                    break
    
    reps = {}
    N = 1e100
    for key, values in data.items():
        arr = numpy.array(values)
        if key == 'time':
            key = 'timesteps'
        reps[key] = arr
        N = min(N, len(arr))
    
    # Ensure equal length arrays in case of partially written 
    # time steps on the log file
    for key in reps.keys():
        reps[key] = reps[key][:N]
    
    if derived:
        if 'Ep' in reps and 'Ek' in reps and 'Et' not in reps:
            N = min(reps['Ek'].size, reps['Ep'].size)
            reps['Et'] = reps['Ek'][:N] + reps['Ep'][:N]
    
    return reps


class OcellarisInspector(wx.Frame):
    def __init__(self, lables, report_names, reports):
        super(OcellarisInspector, self).__init__(None, title='Ocellaris Report Inspector')
        
        self.lables = lables
        self.report_names = report_names
        self.reports = reports
        
        self.layout_widgets()
        self.report_selected()
        
        self.SetSize(800, 800)
        
        self.SetIcon(OCELLARIS_ICON.GetIcon())
        

    def layout_widgets(self):
        p = wx.Panel(self)
        v = wx.BoxSizer(wx.VERTICAL)
        p.SetSizer(v)
        
        # Figure and figure controls
        self.fig = Figure((5.0, 4.0), dpi=100)
        self.canvas = FigureCanvas(p, wx.ID_ANY, self.fig)
        self.axes = self.fig.add_subplot(111)
        toolbar = NavigationToolbar(self.canvas)
        self.plot_cursor_position_info = wx.StaticText(p, style=wx.ALIGN_RIGHT, size=(250, -1), label='')
        self.canvas.mpl_connect('motion_notify_event', self.mouse_position_on_plot)
        v.Add(self.canvas, proportion=1, flag=wx.EXPAND)
        h = wx.BoxSizer(wx.HORIZONTAL)
        h.Add(toolbar, proportion=1)
        h.AddSpacer(10)
        h.Add(self.plot_cursor_position_info, flag=wx.ALIGN_CENTER_VERTICAL)
        h.AddSpacer(5)
        v.Add(h, flag=wx.EXPAND)
        #v.Fit(self)
        
        # Choose report to plot
        h1 = wx.BoxSizer(wx.HORIZONTAL)
        v.Add(h1, flag=wx.ALL|wx.EXPAND, border=4)
        h1.Add(wx.StaticText(p, label='Report:'), flag=wx.ALIGN_CENTER_VERTICAL)
        h1.AddSpacer(5)
        self.report_selector = wx.Choice(p, choices=self.report_names)
        self.report_selector.Select(0)
        self.report_selector.Bind(wx.EVT_CHOICE, self.report_selected)
        h1.Add(self.report_selector, proportion=1)
        
        # Customize the plot text
        Nrows = len(self.lables) + 3
        #Nrows2 = Nrows // 2 + 1 if Nrows % 2 else Nrows // 2
        fgs = wx.FlexGridSizer(rows=Nrows, cols=3, vgap=3, hgap=10)
        fgs.AddGrowableCol(1, proportion=1)
        #fgs.AddGrowableCol(4, proportion=1)
        v.Add(fgs, flag=wx.ALL|wx.EXPAND, border=6)
        
        # Plot title
        fgs.Add(wx.StaticText(p, label='Plot title:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self.title = wx.TextCtrl(p)
        self.title.Bind(wx.EVT_TEXT, self.update_plot)
        fgs.Add(self.title, flag=wx.EXPAND)
        fgs.AddSpacer(0)
        
        # Plot xlabel / log x axis
        fgs.Add(wx.StaticText(p, label='Label X:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self.xlabel = wx.TextCtrl(p)
        self.xlabel.Bind(wx.EVT_TEXT, self.update_plot)
        fgs.Add(self.xlabel, flag=wx.EXPAND)
        self.xlog = wx.CheckBox(p, label='X as log axis')
        self.xlog.Bind(wx.EVT_CHECKBOX, self.update_plot)
        fgs.Add(self.xlog)
        
        # Plot ylabel
        fgs.Add(wx.StaticText(p, label='Label Y:'), flag=wx.ALIGN_CENTER_VERTICAL)
        self.ylabel = wx.TextCtrl(p)
        self.ylabel.Bind(wx.EVT_TEXT, self.update_plot)
        fgs.Add(self.ylabel, flag=wx.EXPAND)
        self.ylog = wx.CheckBox(p, label='Y as log axis')
        self.ylog.Bind(wx.EVT_CHECKBOX, self.update_plot)
        fgs.Add(self.ylog)
        
        # Customize the lables
        self.label_controls = []
        for il, label in enumerate(self.lables):
            fgs.Add(wx.StaticText(p, label='Line %d label:' % il), flag=wx.ALIGN_CENTER_VERTICAL)
            label_ctrl = wx.TextCtrl(p, value=label)
            label_ctrl.Bind(wx.EVT_TEXT, self.update_plot)
            fgs.Add(label_ctrl, flag=wx.EXPAND)
            fgs.Add(wx.StaticText(p, label='(%s)' % label), flag=wx.ALIGN_CENTER_VERTICAL)
            self.label_controls.append(label_ctrl)
        
        v.Fit(p)
        
    def mouse_position_on_plot(self, mpl_event):
        x, y = mpl_event.xdata, mpl_event.ydata
        if x is None or y is None:
            info = ''
        else:
            info = 'pos = (%.6g, %.6g)' % (x, y)
        self.plot_cursor_position_info.Label = info

    def report_selected(self, evt=None):
        irep = self.report_selector.GetSelection()
        report_name = self.report_names[irep]
        
        self.title.ChangeValue('Ocellaris report %s' % report_name)
        self.xlabel.ChangeValue('t')
        self.ylabel.ChangeValue(report_name)
        
        self.update_plot()
        
    def update_plot(self, evt=None):
        irep = self.report_selector.GetSelection()
        report_name = self.report_names[irep]
        
        # How to plot
        xlog = self.xlog.GetValue()
        ylog = self.ylog.GetValue()
        if xlog and ylog:
            plot = self.axes.loglog
        elif xlog:
            plot = self.axes.semilogx
        elif ylog:
            plot = self.axes.semilogy
        else:
            plot = self.axes.plot
        
        self.axes.clear()
        
        lables = [lc.GetValue() for lc in self.label_controls]
        for i, label in enumerate(lables):
            x = self.reports[i]['timesteps']
            if report_name in self.reports[i]:
                y = self.reports[i][report_name]
            else:
                y = numpy.zeros_like(x)
                y[:] = numpy.NaN
            
            plot(x, y, label=label)
        
        self.axes.relim()
        self.axes.autoscale_view()
        self.axes.set_title(self.title.GetValue())
        self.axes.set_xlabel(self.xlabel.GetValue())
        self.axes.set_ylabel(self.ylabel.GetValue())
        self.axes.legend(loc='best')
        self.fig.tight_layout()
        
        self.canvas.draw()


def show_inspector(file_names, lables):
    """
    Show wxPython window that allows chosing  which report to show
    """
    all_reps = []
    all_rep_names = set()
    for fn in file_names:
        reps = read_reports(fn)
        all_reps.append(reps)
        all_rep_names.update(reps.keys())
    report_names = sorted(all_rep_names)
    
    app = wx.App()
    frame = OcellarisInspector(lables, report_names, all_reps)
    frame.Show()
    app.MainLoop()


OCELLARIS_ICON = PyEmbeddedImage(
    "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"
    "DS3/AU3ij6l44vADAAAAAElFTkSuQmCC")


if __name__ == '__main__':
    import sys
    
    # Get report files to save
    h5_file_names = sys.argv[1:]
    
    # Get lables
    lables = []
    for i in range(len(h5_file_names)):
        fn = h5_file_names[i]
        if ':' in fn:
            fn, label = fn.split(':')
            h5_file_names[i] = fn
        else:
            bname = os.path.basename(fn)
            bname_split = bname.split('_endpoint_')
            label = bname_split[0]
        lables.append(label)
    
    # Make plots
    show_inspector(h5_file_names, lables)
