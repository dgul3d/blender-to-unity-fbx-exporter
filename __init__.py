bl_info = {
	"name": "Unity FBX format",
	"author": "Angel 'Edy' Garcia (@VehiclePhysics)",
	"version": (1, 4, 1),
	"blender": (3, 0, 0),
	"location": "File > Export > Unity FBX",
	"description": "FBX exporter compatible with Unity's coordinate and scaling system.",
	"warning": "",
	"wiki_url": "",
	"category": "Import-Export",
}


import bpy
import mathutils
import math


# Multi-user datablocks are preserved here. Unique copies are made for applying the rotation.
# Eventually multi-user datablocks become single-user and gets processed.
# Therefore restoring the multi-user data assigns a shared but already processed datablock.
shared_data = dict()

# All objects and collections in this view layer must be visible while being processed.
# apply_rotation and matrix changes don't have effect otherwise.
# Visibility will be restored right before saving the FBX.
hidden_collections = []
hidden_objects = []
disabled_collections = []
disabled_objects = []


def unhide_collections(col):
	global hidden_collections
	global disabled_collections

	# No need to unhide excluded collections. Their objects aren't included in current view layer.
	if col.exclude:
		return

	# Find hidden child collections and unhide them
	hidden = [item for item in col.children if not item.exclude and item.hide_viewport]
	for item in hidden:
		item.hide_viewport = False

	# Add them to the list so they could be restored later
	hidden_collections.extend(hidden)

	# Same with the disabled collections
	disabled = [item for item in col.children if not item.exclude and item.collection.hide_viewport]
	for item in disabled:
		item.collection.hide_viewport = False

	disabled_collections.extend(disabled)

	# Recursively unhide child collections
	for item in col.children:
		unhide_collections(item)


def unhide_objects():
	global hidden_objects
	global disabled_objects

	view_layer_objects = [ob for ob in bpy.data.objects if ob.name in bpy.context.view_layer.objects]

	for ob in view_layer_objects:
		if ob.hide_get():
			hidden_objects.append(ob)
			ob.hide_set(False)
		if ob.hide_viewport:
			disabled_objects.append(ob)
			ob.hide_viewport = False


def make_single_user_data():
	global shared_data

	for ob in bpy.data.objects:
		if ob.data and ob.data.users > 1:
			# Figure out actual users of this datablock (not counting fake users)
			users = [user for user in bpy.data.objects if user.data == ob.data]
			if len(users) > 1:
				# Store shared mesh data (MESH objects only).
				# Other shared datablocks (CURVE, FONT, etc) are always exported as separate meshes
				# by the built-in FBX exporter.
				if ob.type == 'MESH':
					# Shared mesh data will be restored if users have no active modifiers
					modifiers = 0
					for user in users:
						modifiers += len([mod for mod in user.modifiers if mod.show_viewport])
					if modifiers == 0:
						shared_data[ob.name] = ob.data

				# Single-user data is mandatory in all object types, otherwise we can't apply the rotation.
				ob.data = ob.data.copy()


def apply_object_modifiers():
	# Select objects in current view layer not using an armature modifier
	bpy.ops.object.select_all(action='DESELECT')
	for ob in bpy.data.objects:
		if ob.name in bpy.context.view_layer.objects:
			bypass_modifiers = False
			for mod in ob.modifiers:
				if mod.type == 'ARMATURE':
					bypass_modifiers = True
			if not bypass_modifiers:
				ob.select_set(True)

	# Conversion to mesh may not be available depending on the remaining objects
	if bpy.ops.object.convert.poll():
		print("Converting to meshes:", bpy.context.selected_objects)
		bpy.ops.object.convert(target='MESH')


def reset_parent_inverse(ob):
	if (ob.parent):
		mat_world = ob.matrix_world.copy()
		ob.matrix_parent_inverse.identity()
		ob.matrix_basis = ob.parent.matrix_world.inverted() @ mat_world


def apply_rotation(ob):
	bpy.ops.object.select_all(action='DESELECT')
	ob.select_set(True)
	bpy.ops.object.transform_apply(location = False, rotation = True, scale = False)


def fix_object(ob):
	# Only fix objects in current view layer
	if ob.name in bpy.context.view_layer.objects:

		# Reset parent's inverse so we can work with local transform directly
		reset_parent_inverse(ob)

		# Create a copy of the local matrix and set a pure X-90 matrix
		mat_original = ob.matrix_local.copy()
		ob.matrix_local = mathutils.Matrix.Rotation(math.radians(-90.0), 4, 'X')

		# Apply the rotation to the object
		apply_rotation(ob)

		# Reapply the previous local transform with an X+90 rotation
		ob.matrix_local = mat_original @ mathutils.Matrix.Rotation(math.radians(90.0), 4, 'X')

	# Recursively fix child objects in current view layer.
	# Children may be in the current view layer even if their parent isn't.
	for child in ob.children:
		fix_object(child)


def export_unity_fbx(context, filepath, **kwargs):
	global shared_data
	global hidden_collections
	global hidden_objects
	global disabled_collections
	global disabled_objects

	print("Preparing 3D model for Unity...")

	# Root objects: Empty, Mesh, Curve, Surface, Font or Armature without parent
	root_objects = [item for item in bpy.data.objects if (item.type == "EMPTY" or item.type == "MESH" or item.type == "ARMATURE" or item.type == "FONT" or item.type == "CURVE" or item.type == "SURFACE") and not item.parent]

	# Preserve current scene
	# undo_push examples, including exporters' execute:
	# https://programtalk.com/python-examples/bpy.ops.ed.undo_push  (Examples 4, 5 and 6)
	# https://sourcecodequery.com/example-method/bpy.ops.ed.undo  (Examples 1 and 2)

	bpy.ops.ed.undo_push(message="Prepare Unity FBX")

	shared_data = dict()
	hidden_collections = []
	hidden_objects = []
	disabled_collections = []
	disabled_objects = []

	selection = bpy.context.selected_objects

	# Object mode
	if bpy.ops.object.mode_set.poll():
		bpy.ops.object.mode_set(mode="OBJECT")

	# Ensure all the collections and objects in this view layer are visible
	unhide_collections(bpy.context.view_layer.layer_collection)
	unhide_objects()

	# Create a single copy in multi-user datablocks. Will be restored after fixing rotations.
	make_single_user_data()

	# Apply modifiers to objects (except those affected by an armature)
	apply_object_modifiers()

	try:
		# Fix rotations
		for ob in root_objects:
			print(ob.name, ob.type)
			fix_object(ob)

		# Restore multi-user meshes
		for item in shared_data:
			bpy.data.objects[item].data = shared_data[item]

		# Recompute the transforms out of the changed matrices
		bpy.context.view_layer.update()

		# Restore hidden and disabled objects
		for ob in hidden_objects:
			ob.hide_set(True)
		for ob in disabled_objects:
			ob.hide_viewport = True

		# Restore hidden and disabled collections
		for col in hidden_collections:
			col.hide_viewport = True
		for col in disabled_collections:
			col.collection.hide_viewport = True

		# Restore selection
		bpy.ops.object.select_all(action='DESELECT')
		for ob in selection:
			ob.select_set(True)

		# Export FBX file
		# Start with Unity-specific defaults and override with user settings
		params = dict(
			filepath=filepath,
			apply_scale_options='FBX_SCALE_UNITS',  # Unity-specific default
		)
		# Merge all kwargs (user settings) into params
		params.update(kwargs)
		
		print("Invoking default FBX Exporter:", params)
		bpy.ops.export_scene.fbx(**params)

	except Exception as e:
		bpy.ops.ed.undo_push(message="")
		bpy.ops.ed.undo()
		bpy.ops.ed.undo_push(message="Export Unity FBX")
		print(e)
		print("File not saved.")
		# Always finish with 'FINISHED' so Undo is handled properly
		return {'FINISHED'}

	# Restore scene and finish

	bpy.ops.ed.undo_push(message="")
	bpy.ops.ed.undo()
	bpy.ops.ed.undo_push(message="Export Unity FBX")
	print("FBX file for Unity saved.")
	return {'FINISHED'}


#---------------------------------------------------------------------------------------------------
# Exporter stuff (from the Operator File Export template)

# ExportHelper is a helper class, defines filename and
# invoke() function which calls the file selector.
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator


class ExportUnityFbx(Operator, ExportHelper):
	"""FBX exporter compatible with Unity's coordinate and scaling system"""
	bl_idname = "export_scene.unity_fbx"
	bl_label = "Export Unity FBX"
	bl_options = {'UNDO_GROUPED'}

	# ExportHelper mixin class uses this
	filename_ext = ".fbx"

	filter_glob: StringProperty(
		default="*.fbx",
		options={'HIDDEN'},
		maxlen=255,  # Max internal buffer length, longer would be clamped.
	)

	# List of operator properties, the attributes will be assigned
	# to the class instance from the operator settings before calling.

	# Selection properties
	use_selection: BoolProperty(
		name="Selected Objects",
		description="Export selected and visible objects only",
		default=False,
	)

	use_visible: BoolProperty(
		name='Visible Objects',
		description='Export visible objects only',
		default=False
	)

	use_active_collection: BoolProperty(
		name="Active Collection",
		description="Export only objects from the active collection (and its children)",
		default=False,
	)

	# Legacy property names for backwards compatibility
	@property
	def active_collection(self):
		return self.use_active_collection

	@active_collection.setter
	def active_collection(self, value):
		self.use_active_collection = value

	@property
	def selected_objects(self):
		return self.use_selection

	@selected_objects.setter
	def selected_objects(self, value):
		self.use_selection = value

	# Transform properties
	global_scale: bpy.props.FloatProperty(
		name="Scale",
		description="Scale all data (Some importers do not support scaled armatures!)",
		min=0.001, max=1000.0,
		soft_min=0.01, soft_max=1000.0,
		default=1.0,
	)

	apply_unit_scale: BoolProperty(
		name="Apply Unit",
		description="Take into account current Blender units settings (if unset, raw Blender Units values are used as-is)",
		default=True,
	)

	use_space_transform: BoolProperty(
		name="Use Space Transform",
		description="Apply global space transform to the object rotations. When disabled only the axis space is written to the file and all object transforms are left as-is",
		default=True,
	)

	# Object types
	object_types: EnumProperty(
		name="Object Types",
		options={'ENUM_FLAG'},
		items=(('EMPTY', "Empty", ""),
			   ('CAMERA', "Camera", ""),
			   ('LIGHT', "Lamp", ""),
			   ('ARMATURE', "Armature", "WARNING: not supported in dupli/group instances"),
			   ('MESH', "Mesh", ""),
			   ('OTHER', "Other", "Other geometry types, like curve, metaball, etc. (converted to meshes)"),
			   ),
		description="Which kind of object to export",
		default={'EMPTY', 'CAMERA', 'LIGHT', 'ARMATURE', 'MESH', 'OTHER'},
	)

	# Mesh properties
	use_mesh_modifiers: BoolProperty(
		name="Apply Modifiers",
		description="Apply modifiers to mesh objects (except Armature ones) - WARNING: prevents exporting shape keys",
		default=True,
	)

	use_mesh_modifiers_render: BoolProperty(
		name="Use Modifiers Render Setting",
		description="Use render settings when applying modifiers to mesh objects (DISABLED in Blender 2.8)",
		default=True,
	)

	mesh_smooth_type: EnumProperty(
		name="Smoothing",
		items=(('OFF', "Normals Only", "Export only normals instead of writing edge or face smoothing data"),
			   ('FACE', "Face", "Write face smoothing"),
			   ('EDGE', "Edge", "Write edge smoothing"),
			   ),
		description="Export smoothing information (prefer 'Normals Only' option if your target importer understand split normals)",
		default='OFF',
	)

	colors_type: EnumProperty(
		name="Vertex Colors",
		items=(('NONE', "None", "Do not export color attributes"),
			   ('SRGB', "sRGB", "Export colors in sRGB color space"),
			   ('LINEAR', "Linear", "Export colors in linear color space"),
			   ),
		description="Export vertex color attributes",
		default='SRGB',
	)

	prioritize_active_color: BoolProperty(
		name="Prioritize Active Color",
		description="Make sure active color will be exported first. Could be important since some other software can discard other color attributes besides the first one",
		default=False,
	)

	use_subsurf: BoolProperty(
		name="Export Subdivision Surface",
		description="Export the last Catmull-Rom subdivision modifier as FBX subdivision (does not apply the modifier even if 'Apply Modifiers' is enabled)",
		default=False,
	)

	use_mesh_edges: BoolProperty(
		name="Loose Edges",
		description="Export loose edges (as two-vertices polygons)",
		default=False,
	)

	use_custom_props: BoolProperty(
		name="Custom Properties",
		description="Export custom properties",
		default=False,
	)

	# Armature properties
	use_armature_deform_only: BoolProperty(
		name="Only Deform Bones",
		description="Only write deforming bones (and non-deforming ones when they have deforming children)",
		default=False,
	)

	# Legacy property for backwards compatibility
	@property
	def deform_bones(self):
		return self.use_armature_deform_only

	@deform_bones.setter
	def deform_bones(self, value):
		self.use_armature_deform_only = value

	add_leaf_bones: BoolProperty(
		name="Add Leaf Bones",
		description="Append a final bone to the end of each chain to specify last bone length (use this when you intend to edit the armature from exported data)",
		default=False,
	)

	# Legacy property for backwards compatibility
	@property
	def leaf_bones(self):
		return self.add_leaf_bones

	@leaf_bones.setter
	def leaf_bones(self, value):
		self.add_leaf_bones = value

	armature_nodetype: EnumProperty(
		name="Armature FBXNode Type",
		items=(('NULL', "Null", "'Null' FBX node, similar to Blender's Empty (default)"),
			   ('ROOT', "Root", "'Root' FBX node, supposed to be the root of chains of bones..."),
			   ('LIMBNODE', "LimbNode", "'LimbNode' FBX node, a regular joint between two bones..."),
			   ),
		description="FBX type of node (object) used to represent Blender's armatures (use the Null type unless you experience issues with the other app, as other choices may not import back perfectly into Blender...)",
		default='NULL',
	)

	primary_bone_axis: EnumProperty(
		name="Primary",
		items=(('X', "X Axis", ""),
				('Y', "Y Axis", ""),
				('Z', "Z Axis", ""),
				('-X', "-X Axis", ""),
				('-Y', "-Y Axis", ""),
				('-Z', "-Z Axis", ""),
		),
		default='Y',
	)

	secondary_bone_axis: EnumProperty(
		name="Secondary",
		items=(('X', "X Axis", ""),
				('Y', "Y Axis", ""),
				('Z', "Z Axis", ""),
				('-X', "-X Axis", ""),
				('-Y', "-Y Axis", ""),
				('-Z', "-Z Axis", ""),
		),
		default='X',
	)

	use_tspace: BoolProperty(
		name="Tangent Space",
		description="Add binormal and tangent vectors, together with normal they form the tangent space (will only work correctly with tris/quads only meshes!)",
		default=False,
	)

	# Legacy property for backwards compatibility
	@property
	def tangent_space(self):
		return self.use_tspace

	@tangent_space.setter
	def tangent_space(self, value):
		self.use_tspace = value

	use_triangles: BoolProperty(
		name="Triangulate Faces",
		description="Convert all faces to triangles",
		default=False,
	)

	# Legacy property for backwards compatibility
	@property
	def triangulate_faces(self):
		return self.use_triangles

	@triangulate_faces.setter
	def triangulate_faces(self, value):
		self.use_triangles = value

	# Path and texture properties
	path_mode: EnumProperty(
		name="Path Mode",
		items=(('AUTO', "Auto", "Use relative paths with subdirectories only"),
			   ('ABSOLUTE', "Absolute", "Always write absolute paths"),
			   ('RELATIVE', "Relative", "Always write relative paths (where possible)"),
			   ('MATCH', "Match", "Match absolute/relative setting with input path"),
			   ('STRIP', "Strip Path", "Filename only"),
			   ('COPY', "Copy", "Copy the file to the destination path (or subdirectory)"),
			   ),
		description="Method used to reference paths",
		default='AUTO',
	)

	embed_textures: BoolProperty(
		name="Embed Textures",
		description="Embed textures in FBX binary file (only for \"Copy\" path mode!)",
		default=False,
	)

	# Batch export properties
	batch_mode: EnumProperty(
		name="Batch Mode",
		items=(('OFF', "Off", "Active scene to file"),
			   ('SCENE', "Scene", "Each scene as a file"),
			   ('COLLECTION', "Collection", "Each collection (data-block ones) as a file, does not include content of children collections"),
			   ('SCENE_COLLECTION', "Scene Collections", "Each collection (including master, non-data-block ones) of each scene as a file, including content from children collections"),
			   ('ACTIVE_SCENE_COLLECTION', "Active Scene Collections", "Each collection (including master, non-data-block one) of the active scene as a file, including content from children collections"),
			   ),
		default='OFF',
	)

	use_batch_own_dir: BoolProperty(
		name="Batch Own Dir",
		description="Create a dir for each exported file",
		default=True,
	)

	use_metadata: BoolProperty(
		name="Use Metadata",
		default=True,
		options={'HIDDEN'},
	)

	# Animation properties
	bake_anim: BoolProperty(
		name="Baked Animation",
		description="Export baked keyframe animation",
		default=True,
	)

	bake_anim_use_all_bones: BoolProperty(
		name="Key All Bones",
		description="Force exporting at least one key of animation for all bones (needed with some target applications, like Unity)",
		default=True,
	)

	bake_anim_use_nla_strips: BoolProperty(
		name="NLA Strips",
		description="Export each non-muted NLA strip as a separated FBX's AnimStack, if any, instead of global scene animation",
		default=True,
	)

	bake_anim_use_all_actions: BoolProperty(
		name="All Actions",
		description="Export each action as a separated FBX's AnimStack, instead of global scene animation (note that animated objects will get all actions compatible with them, others will get no animation at all)",
		default=True,
	)

	bake_anim_force_startend_keying: BoolProperty(
		name="Force Start/End Keying",
		description="Always add a keyframe at start and end of actions for animated channels",
		default=True,
	)

	bake_anim_step: bpy.props.FloatProperty(
		name="Sampling Rate",
		description="How often to evaluate animated values (in frames)",
		min=0.01, max=100.0,
		soft_min=0.1, soft_max=10.0,
		default=1.0,
	)

	bake_anim_simplify_factor: bpy.props.FloatProperty(
		name="Simplify",
		description="How much to simplify baked values (0.0 to disable, the higher the more simplified)",
		min=0.0, max=100.0,
		soft_min=0.0, soft_max=10.0,
		default=1.0,
	)

	# Custom draw method
	# https://blender.stackexchange.com/questions/55437/add-gui-elements-to-exporter-window
	# https://docs.blender.org/api/current/bpy.types.UILayout.html

	def draw(self, context):
		layout = self.layout
		layout.use_property_split = True
		layout.use_property_decorate = False
		
		# Main settings
		layout.prop(self, "path_mode")
		row = layout.row(align=True)
		row.enabled = (self.path_mode == 'COPY')
		row.prop(self, "embed_textures", text="", icon='PACKAGE' if self.embed_textures else 'UGLYPACKAGE')
		
		layout.prop(self, "batch_mode")
		layout.prop(self, "use_batch_own_dir")
		
		layout.separator()
		
		# Include section
		box = layout.box()
		box.label(text="Include", icon='FILTER')
		col = box.column(heading="Limit to")
		col.prop(self, "use_selection", text="Selected Objects")
		col.prop(self, "use_visible", text="Visible Objects")
		col.prop(self, "use_active_collection", text="Active Collection")
		box.prop(self, "object_types")
		box.prop(self, "use_custom_props")
		
		layout.separator()
		
		# Transform section
		box = layout.box()
		box.label(text="Transform", icon='OBJECT_ORIGIN')
		box.prop(self, "global_scale")
		box.prop(self, "apply_unit_scale")
		box.prop(self, "use_space_transform")
		
		layout.separator()
		
		# Geometry section
		box = layout.box()
		box.label(text="Geometry", icon='MESH_DATA')
		box.prop(self, "mesh_smooth_type")
		box.prop(self, "use_subsurf")
		box.prop(self, "use_mesh_modifiers")
		sub = box.row()
		sub.enabled = self.use_mesh_modifiers
		sub.prop(self, "use_mesh_modifiers_render")
		box.prop(self, "use_mesh_edges")
		box.prop(self, "use_triangles")
		box.prop(self, "use_tspace")
		box.prop(self, "colors_type")
		box.prop(self, "prioritize_active_color")
		
		layout.separator()
		
		# Armature section
		box = layout.box()
		box.label(text="Armature", icon='ARMATURE_DATA')
		box.prop(self, "primary_bone_axis")
		box.prop(self, "secondary_bone_axis")
		box.prop(self, "armature_nodetype")
		box.prop(self, "use_armature_deform_only")
		box.prop(self, "add_leaf_bones")
		
		layout.separator()
		
		# Animation section
		box = layout.box()
		row = box.row()
		row.prop(self, "bake_anim", text="")
		row.label(text="Animation", icon='ANIM')
		
		col = box.column()
		col.enabled = self.bake_anim
		col.prop(self, "bake_anim_use_all_bones")
		col.prop(self, "bake_anim_use_nla_strips")
		col.prop(self, "bake_anim_use_all_actions")
		col.prop(self, "bake_anim_force_startend_keying")
		col.prop(self, "bake_anim_step")
		col.prop(self, "bake_anim_simplify_factor")

	def execute(self, context):
		# Collect all export properties as kwargs
		kwargs = {
			'use_selection': self.use_selection,
			'use_visible': self.use_visible,
			'use_active_collection': self.use_active_collection,
			'global_scale': self.global_scale,
			'apply_unit_scale': self.apply_unit_scale,
			'use_space_transform': self.use_space_transform,
			'object_types': self.object_types,
			'use_mesh_modifiers': self.use_mesh_modifiers,
			'use_mesh_modifiers_render': self.use_mesh_modifiers_render,
			'mesh_smooth_type': self.mesh_smooth_type,
			'colors_type': self.colors_type,
			'prioritize_active_color': self.prioritize_active_color,
			'use_subsurf': self.use_subsurf,
			'use_mesh_edges': self.use_mesh_edges,
			'use_custom_props': self.use_custom_props,
			'use_armature_deform_only': self.use_armature_deform_only,
			'add_leaf_bones': self.add_leaf_bones,
			'armature_nodetype': self.armature_nodetype,
			'primary_bone_axis': self.primary_bone_axis,
			'secondary_bone_axis': self.secondary_bone_axis,
			'use_tspace': self.use_tspace,
			'use_triangles': self.use_triangles,
			'path_mode': self.path_mode,
			'embed_textures': self.embed_textures,
			'batch_mode': self.batch_mode,
			'use_batch_own_dir': self.use_batch_own_dir,
			'use_metadata': self.use_metadata,
			'bake_anim': self.bake_anim,
			'bake_anim_use_all_bones': self.bake_anim_use_all_bones,
			'bake_anim_use_nla_strips': self.bake_anim_use_nla_strips,
			'bake_anim_use_all_actions': self.bake_anim_use_all_actions,
			'bake_anim_force_startend_keying': self.bake_anim_force_startend_keying,
			'bake_anim_step': self.bake_anim_step,
			'bake_anim_simplify_factor': self.bake_anim_simplify_factor,
		}
		return export_unity_fbx(context, self.filepath, **kwargs)


# FileHandler for Collection Exporter support
class IO_FH_unity_fbx(bpy.types.FileHandler):
	"""File handler for Unity FBX format - enables Collection Exporter integration"""
	bl_idname = "IO_FH_unity_fbx"
	bl_label = "Unity FBX"
	bl_import_operator = ""  # No import operator
	bl_export_operator = "export_scene.unity_fbx"
	bl_file_extensions = ".fbx"

	@classmethod
	def poll_drop(cls, context):
		"""Check if we can handle dropped files"""
		return False  # Unity FBX is export-only


# Only needed if you want to add into a dynamic menu
def menu_func_export(self, context):
	self.layout.operator(ExportUnityFbx.bl_idname, text="Unity FBX (.fbx)")


classes = (
	ExportUnityFbx,
	IO_FH_unity_fbx,
)


def register():
	for cls in classes:
		bpy.utils.register_class(cls)
	bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
	bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)


if __name__ == "__main__":
	register()

	# test call
	bpy.ops.export_scene.unity_fbx('INVOKE_DEFAULT')
