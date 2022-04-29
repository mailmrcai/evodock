import logging

from pyrosetta import Pose, Vector1
from pyrosetta.rosetta.protocols.docking import (DockingSlideIntoContact,
                                                 DockMCMProtocol,
                                                 calc_interaction_energy,
                                                 calc_Irmsd)
from src.differential_evolution import Individual
from src.utils import get_position_info
from pyrosetta.rosetta.core.pose.symmetry import is_symmetric
from pyrosetta.rosetta.protocols.symmetry import FaSymDockingSlideTogether
from src.symmetry import SymDockMCMProtocol, SymDockingSlideIntoContactWrapper, SequentialSymmetrySliderWrapper
from pyrosetta.rosetta.core.conformation.symmetry import SlideCriteriaType
from pyrosetta.rosetta.protocols.symmetry import SymmetrySlider
from pyrosetta.rosetta.protocols.symmetry import SequentialSymmetrySlider
from pyrosetta import PyMOLMover
from src.utils import IP_ADDRESS


class LocalSearchPopulation:
    # Options:
    # None: only score and return the poses
    # only_slide: just slide_into_contact
    # mcm_rosetta: mcm protocol mover (high res) from rosetta (2 cycles)
    def __init__(self, scfxn, packer_option="default_combination", slide=True, show_local_search=False,
        pymol_history=False):
        self.packer_option = packer_option
        self.scfxn = scfxn
        self.local_logger = logging.getLogger("evodock.local")
        self.local_logger.setLevel(logging.INFO)
        self.slide = slide
        self.show_local_search = show_local_search
        self.pymol_history = pymol_history

        if is_symmetric(scfxn.dock_pose):
            self.slide_into_contact = SequentialSymmetrySlider(scfxn.dock_pose, SlideCriteriaType(1))

            # This will only slide on the first pose!!
            # FA_REP_SCORE = SlideCriteriaType(2)
            # self.slide_into_contact = SequentialSymmetrySlider(scfxn.dock_pose, FA_REP_SCORE)

            # todo: use the highresolution alternative below or delete it
            # dofs = scfxn.dock_pose.conformation().Symmetry_Info().get_dofs()
            # self.slide_into_contact = FaSymDockingSlideTogether(dofs)
        else:
            self.slide_into_contact = DockingSlideIntoContact(1)
        if packer_option == "mcm_rosetta":
            if is_symmetric(scfxn.dock_pose):
                self.docking = SymDockMCMProtocol(scfxn.dock_pose)
            else:
                mcm_docking = DockMCMProtocol()
                mcm_docking.set_native_pose(scfxn.dock_pose)
                mcm_docking.set_scorefxn(scfxn.scfxn_rosetta)
                mcm_docking.set_rt_min(False)
                mcm_docking.set_sc_min(False)
                mock_pose = Pose()
                mock_pose.assign(scfxn.dock_pose)
                mcm_docking.apply(mock_pose)
                self.docking = mcm_docking
                # DEBUG Is this a hack to skip the create_and_attach_task_factory on each apply to save time??
                # A reason for potentially deleting this is that it is quite confusing. The taskfactory is already set when
                # calling apply above (see protocols.docking.DockMCMProtocol.cc:179). It is the default one in this case.
                # Then the taskfacory is set again using the default one, in a way that is intented for a new tasks using
                # task_factory(). Although this seems to actually surpass the creation of  the task_factory on each apply
                # so it could be considered smart.
                self.docking.set_task_factory(mcm_docking.task_factory())
                self.docking.set_ignore_default_task(True)

    def energy_score(self, pose):
        score = self.scfxn.scfxn_rosetta(pose)
        return score

    def process_individual(self, ind, local_search=True):
        pose = self.scfxn.apply_genotype_to_pose(ind)
        before = self.energy_score(pose)
        if local_search and self.packer_option != "None":
            if self.show_local_search:
                self.pymol_pose_visualization(pose, description="local_search_init")
            if self.slide:
                self.slide_into_contact.apply(pose)
                if self.show_local_search:
                    self.pymol_pose_visualization(pose, description="local_search_post_slide")
            if self.packer_option != "only_slide":
                self.docking.apply(pose)
                if self.show_local_search:
                    self.pymol_pose_visualization(pose, description="local_search_post_docking")
            after = self.energy_score(pose)
        else:
            after = before

        rmsd = self.scfxn.get_rmsd(pose)

        interface = calc_interaction_energy(
            pose, self.scfxn.scfxn_rosetta, Vector1([1])
        )
        irms = calc_Irmsd(
            self.scfxn.native_pose, pose, self.scfxn.scfxn_rosetta, Vector1([1])
        )

        # get position from pose
        positions = get_position_info(pose)
        # replace trial with this new positions
        genotype = self.scfxn.convert_positions_to_genotype(positions)
        result_individual = Individual(genotype, after, rmsd, interface, irms)
        return result_individual, before, after

    def pymol_pose_visualization(self, pose, history=False, description=""):
        pymover = PyMOLMover(address=IP_ADDRESS, port=65000, max_packet_size=1400)
        if self.pymol_history:
            pymover.keep_history(True)
        tmp_pose = pose.clone()
        tmp_pose.pdb_info().name(description)
        pymover.apply(tmp_pose)
