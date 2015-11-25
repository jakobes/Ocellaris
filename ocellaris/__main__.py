from ocellaris import get_version, get_detailed_version, Simulation, setup_simulation, run_simulation


def main(inputfile, input_override):
    """
    Run Ocellaris
    """
    sim = Simulation()
    
    # Read input
    if sim.io.is_restart_file(inputfile):
        sim.io.load_restart_file_input(inputfile)
    else:
        sim.input.read_yaml(inputfile)
    
    # Alter input by values given on the command line
    override_input_variables(sim, input_override)
    
    # Setup logging before we start printing anything
    sim.log.setup()
    
    # Print banner with Ocellaris version number 
    version = get_detailed_version() or get_version()
    sim.log.info('='*80)
    sim.log.info('                  Ocellaris   %s' % version) 
    sim.log.info('='*80)
    sim.log.info()
    
    # Setup the Ocellaris simulation
    setup_simulation(sim, setup_logging=False, catch_exceptions=True)
    
    if sim.restarted:
        # Load previous results
        sim.io.load_restart_file_results(inputfile)
    
    # Run the Ocellaris simulation time loop
    run_simulation(sim, catch_exceptions=True)
    
    sim.log.info('='*80)
    if sim.success:
        sim.log.info('Ocellaris finished successfully')
    else:
        sim.log.info('Ocellaris finished with errors')


def override_input_variables(simulation, input_override):
    """
    The user can override values given on the input file via
    command line parameters like::
    
        --set-input time/dt=0.1
        
    This code updates the input dictionary with these modifications
    """
    if input_override is None:
        return
    
    def conv_path_element(element):
        """
        Take into account that the path element may
        be a list index given as an integer
        """ 
        try:
            return int(elem)
        except ValueError:
            return element
    
    for overrider in input_override:
        # Overrider is something like "time/dt=0.1"
        path = overrider.split('=')[0]
        value = overrider[len(path)+1:]
        
        # Find the correct input sub-dictionary
        path_elements = path.split('/')
        d = simulation.input
        for elem in path_elements[:-1]:
            # Take into account that the path element may
            # be a list index given as an integer 
            try:
                elem = int(elem)
            except ValueError:
                pass
            
            # Extract the sub-dictionary (or list)
            idx = conv_path_element(elem)
            if not idx in d:
                d[idx] = {}
            d = d[idx]
        
        # Convert value to Python object
        try:
            py_value = eval(value)
        except Exception as e:
            print 'ERROR: Input variable given via command line argument failed:'
            print 'ERROR:       --set-input "%s"' % overrider
            print 'ERROR: Got exception: %s' % str(e)
            exit(-1)
        
        # Update the input sub-dictionary
        idx = conv_path_element(path_elements[-1])
        d[idx] = py_value


if __name__ == '__main__':
    # Get command line arguments
    import argparse
    parser = argparse.ArgumentParser(prog='ocellaris',
                                     description='Discontinuous Galerkin Navier-Stokes solver')
    parser.add_argument('inputfile', help='Name of file containing simulation '
                        'configuration on the Ocellaris YAML input format')
    parser.add_argument('--set-input', '-i', action='append', help='Set an inpup key. Can be added several '
                        'times to set multiple input keys. Example: --set-input time/dt=0.1')
    
    args = parser.parse_args()
    
    # Run Ocellaris
    main(args.inputfile, args.set_input)
