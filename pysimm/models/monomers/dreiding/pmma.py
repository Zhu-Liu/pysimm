from pysimm import system, lmps, forcefield
from pysimm.apps.random_walk import random_walk

def monomer():
    s = system.read_pubchem_smiles('CC(C)C(=O)OC')
    f = forcefield.Dreiding()
    
    s.apply_forcefield(f)
    
    c3 = s.particles[3]
    c4 = s.particles[4]
    
    for b in c3.bonds:
        if b.a.elem == 'H' or b.b.elem == 'H':
            pb = b.a if b.b is c3 else b.b
            s.particles.remove(pb.tag, update=False)
            break
        
    for b in c4.bonds:
        if b.a.elem == 'H' or b.b.elem == 'H':
            pb = b.a if b.b is c4 else b.b
            s.particles.remove(pb.tag, update=False)
            break
            
    s.remove_spare_bonding()

    c3.linker = 'head'
    c4.linker = 'tail'
    
    lmps.quick_min(s, min_style='fire')
    
    s.add_particle_bonding()
    
    return s
    
def polymer_chain(length):
    mon = monomer()
    polym = random_walk(mon, length, forcefield=forcefield.Dreiding())
    return polym