from dolfin import compile_extension_module
import numpy
import os

def _get_cpp_module(source_dir, header_files, source_files):
    """
    Use the dolfin machinery to compile, wrap with swig and load a c++ module
    """
    cpp_dir = os.path.dirname(os.path.abspath(__file__))

    source_dir = os.path.join(cpp_dir, source_dir)
    
    header_sources = []
    for hpp_filename in header_files:
        hpp_filename = os.path.join(source_dir, hpp_filename)
        
        with open(hpp_filename, 'rt') as f:
            hpp_code = f.read()
        header_sources.append(hpp_code)
        
    try:
        module = compile_extension_module(code='\n\n\n'.join(header_sources),
                                          source_directory=source_dir, 
                                          sources=source_files,
                                          include_dirs=[".", source_dir])
    except RuntimeError, e:
        COMPILE_ERROR = "In instant.recompile: The module did not compile with command 'make VERBOSE=1', see "
        if e.message.startswith(COMPILE_ERROR):
            # Get the path of the error file
            path = e.message.split("'")[-2]
            # Print the error file
            with open(path, 'rt') as error:
                print error.read()
            raise
        
    return module

class _ModuleCache(object):
    def __init__(self):
        """
        A registry and cache of available C/C++ extension modules
        """
        self.available_modules = {}
        self.module_cache = {}
    
    def add_module(self, name, source_dir, header_files, source_files):
        """
        Add a module that can be compiled
        """
        self.available_modules[name] = (source_dir, header_files, source_files)
        
    def get_module(self, name, reload=False):
        """
        Compile and load a module (first time) or use from cache (subsequent requests)
        """
        if reload or name not in self.module_cache:
            source_dir, header_files, source_files = self.available_modules[name]
            mod = _get_cpp_module(source_dir, header_files, source_files)
            self.module_cache[name] = mod
        
        return self.module_cache[name]

###############################################################################################
# Functions to be used by other modules

_MODULES = _ModuleCache()
_MODULES.add_module('gradient_reconstruction', 'gradient_reconstruction', ['gradient_reconstruction.h'], ['gradient_reconstruction.cpp'])

def load_module(name, reload=False):
    """
    Load the C/C++ module registered with the given name. Reload
    forces a cache-refresh, otherwise subsequent accesses are cached
    """
    return _MODULES.get_module(name, reload)