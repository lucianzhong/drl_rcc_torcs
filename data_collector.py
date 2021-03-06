#!/usr/bin/python
# snakeoil.py
# Chris X Edwards <snakeoil@xed.ch>
# Snake Oil is a Python library for interfacing with a TORCS
# race car simulator which has been patched with the server
# extentions used in the Simulated Car Racing competitions.
# http://scr.geccocompetitions.com/
#
# To use it, you must import it and create a "drive()" function.
# This will take care of option handling and server connecting, etc.
# To see how to write your own client do something like this which is
# a complete working client:
# /-----------------------------------------------\
# |#!/usr/bin/python                              |
# |import snakeoil                                |
# |if __name__ == "__main__":                     |
# |    C= snakeoil.Client()                       |
# |    for step in xrange(C.maxSteps,0,-1):       |
# |        C.get_servers_input()                  |
# |        snakeoil.drive_example(C)              |
# |        C.respond_to_server()                  |
# |    C.shutdown()                               |
# \-----------------------------------------------/
# This should then be a full featured client. The next step is to
# replace 'snakeoil.drive_example()' with your own. There is a
# dictionary which holds various option values (see `default_options`
# variable for all the details) but you probably only need a few
# things from it. Mainly the `trackname` and `stage` are important
# when developing a strategic bot.
#
# This dictionary also contains a ServerState object
# (key=S) and a DriverAction object (key=R for response). This allows
# you to get at all the information sent by the server and to easily
# formulate your reply. These objects contain a member dictionary "d"
# (for data dictionary) which contain key value pairs based on the
# server's syntax. Therefore, you can read the following:
#    angle, curLapTime, damage, distFromStart, distRaced, focus,
#    fuel, gear, lastLapTime, opponents, racePos, rpm,
#    speedX, speedY, speedZ, track, trackPos, wheelSpinVel, z
# The syntax specifically would be something like:
#    X= o[S.d['tracPos']]
# And you can set the following:
#    accel, brake, clutch, gear, steer, focus, meta
# The syntax is:
#     o[R.d['steer']]= X
# Note that it is 'steer' and not 'steering' as described in the manual!
# All values should be sensible for their type, including lists being lists.
# See the SCR manual or http://xed.ch/help/torcs.html for details.
#
# If you just run the snakeoil.py base library itself it will implement a
# serviceable client with a demonstration drive function that is
# sufficient for getting around most tracks.
# Try `snakeoil.py --help` to get started.

# for Python3-based torcs python robot client
import numpy as np
import socket
import sys
import getopt
import os
import os.path
import time
import collections as col
import pickle as plk
from scripts.autostart import TorcsInstance
from network import *
from torch.autograd import Variable
from data_feeder import *
from sync import *

PI= 3.14159265359

data_size = 2**17

# Initialize help messages
ophelp=  'Options:\n'
ophelp+= ' --host, -H <host>    TORCS server host. [localhost]\n'
ophelp+= ' --port, -p <port>    TORCS port. [3001]\n'
ophelp+= ' --id, -i <id>        ID for server. [SCR]\n'
ophelp+= ' --steps, -m <#>      Maximum simulation steps. 1 sec ~ 50 steps. [100000]\n'
ophelp+= ' --episodes, -e <#>   Maximum learning episodes. [1]\n'
ophelp+= ' --track, -t <track>  Your name for this track. Used for learning. [unknown]\n'
ophelp+= ' --stage, -s <#>      0=warm up, 1=qualifying, 2=race, 3=unknown. [3]\n'
ophelp+= ' --debug, -d          Output full telemetry.\n'
ophelp+= ' --help, -h           Show this help.\n'
ophelp+= ' --version, -v        Show current version.\n'
ophelp+= ' --random, -r         Random choose track and speed.'
ophelp+= ' --training, -x       Choose steering according to vision and traing in respect to the real value.'
usage= 'Usage: %s [ophelp [optargs]] \n' % sys.argv[0]
usage= usage + ophelp
version= "20130505-2"

def clip(v,lo,hi):
	if v<lo: return lo
	elif v>hi: return hi
	else: return v

def bargraph(x,mn,mx,w,c='X'):
	'''Draws a simple asciiart bar graph. Very handy for
	visualizing what's going on with the data.
	x= Value from sensor, mn= minimum plottable value,
	mx= maximum plottable value, w= width of plot in chars,
	c= the character to plot with.'''
	if not w: return '' # No width!
	if x<mn: x= mn      # Clip to bounds.
	if x>mx: x= mx      # Clip to bounds.
	tx= mx-mn # Total real units possible to show on graph.
	if tx<=0: return 'backwards' # Stupid bounds.
	upw= tx/float(w) # X Units per output char width.
	if upw<=0: return 'what?' # Don't let this happen.
	negpu, pospu, negnonpu, posnonpu= 0,0,0,0
	if mn < 0: # Then there is a negative part to graph.
		if x < 0: # And the plot is on the negative side.
			negpu= -x + min(0,mx)
			negnonpu= -mn + x
		else: # Plot is on pos. Neg side is empty.
			negnonpu= -mn + min(0,mx) # But still show some empty neg.
	if mx > 0: # There is a positive part to the graph
		if x > 0: # And the plot is on the positive side.
			pospu= x - max(0,mn)
			posnonpu= mx - x
		else: # Plot is on neg. Pos side is empty.
			posnonpu= mx - max(0,mn) # But still show some empty pos.
	nnc= int(negnonpu/upw)*'-'
	npc= int(negpu/upw)*c
	ppc= int(pospu/upw)*c
	pnc= int(posnonpu/upw)*'_'
	return '[%s]' % (nnc+npc+ppc+pnc)

class Client():
	def __init__(self,H=None,p=None,i=None,e=None,t=None,s=None,d=None,r=None,x=None, maxspeed=30,track=1,vision=True, model=""):
		# If you don't like the option defaults,  change them here.
		self.vision = vision

		self.host= 'localhost'
		self.port= 3001
		self.sid= 'SCR'
		self.maxEpisodes=1 # "Maximum number of learning episodes to perform"
		self.trackname= 'unknown'
		self.stage= 3 # 0=Warm-up, 1=Qualifying 2=Race, 3=unknown <Default=3>
		self.debug= False
		self.maxSteps= 7000  # 50steps/second
		self.randomTrackSpeed = False
		self.maxSpeed = maxspeed
		self.training = False
		self.parse_the_command_line()
		if H: self.host= H
		if p: self.port= p
		if i: self.sid= i
		if e: self.maxEpisodes= e
		if t: self.trackname= t
		if s: self.stage= s
		if d: self.debug= d
		if x: self.training= x
		if self.training:
			model_name = "models/#track=%d#speed=%d.model" % (track, maxspeed)
			if model:
				model_name = model
			print("Loading %s " % model_name)
			self.network = getNetwork(model_file=model_name)
		self.S= ServerState()
		self.R= DriverAction()
		self.setup_connection()

	def setup_connection(self):
		# == Set Up UDP Socket ==
		try:
			self.so= socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		except socket.error as emsg:
			print('Error: Could not create socket...')
			sys.exit(-1)
		# == Initialize Connection To Server ==
		self.so.settimeout(1)

		torcs_instance = TorcsInstance()
		torcs_instance.start()

		n_fail = 5
		while True:
			# This string establishes track sensor angles! You can customize them.
			#a= "-90 -75 -60 -45 -30 -20 -15 -10 -5 0 5 10 15 20 30 45 60 75 90"
			# xed- Going to try something a bit more aggressive...
			a= "-45 -19 -12 -7 -4 -2.5 -1.7 -1 -.5 0 .5 1 1.7 2.5 4 7 12 19 45"

			initmsg='%s(init %s)' % (self.sid,a)

			try:
				self.so.sendto(initmsg.encode(), (self.host, self.port))
			except socket.error as emsg:
				sys.exit(-1)
			sockdata= str()
			try:
				sockdata,addr= self.so.recvfrom(data_size)
				sockdata = sockdata.decode('utf-8')
			except socket.error as emsg:
				print("Waiting for server on %d............" % self.port)
				print("Count Down : " + str(n_fail))
				if n_fail < 0:
					torcs_instance.start()
					n_fail = 5
				n_fail -= 1

			identify = '***identified***'
			if identify in sockdata:
				print("Client connected on %d.............." % self.port)
				break

	def parse_the_command_line(self):
		try:
			(opts, args) = getopt.getopt(sys.argv[1:], 'H:p:i:m:e:t:r:s:x:dhv',
					   ['host=','port=','id=','steps=',
						'episodes=','track=','stage=',
						'debug','help','version', 'random', 'training'])
		except getopt.error as why:
			print('getopt error: %s\n%s' % (why, usage))
			sys.exit(-1)
		try:
			for opt in opts:
				if opt[0] == '-h' or opt[0] == '--help':
					print(usage)
					sys.exit(0)
				if opt[0] == '-d' or opt[0] == '--debug':
					self.debug= True
				if opt[0] == '-H' or opt[0] == '--host':
					self.host= opt[1]
				if opt[0] == '-i' or opt[0] == '--id':
					self.sid= opt[1]
				if opt[0] == '-t' or opt[0] == '--track':
					self.trackname= opt[1]
				if opt[0] == '-s' or opt[0] == '--stage':
					self.stage= int(opt[1])
				if opt[0] == '-p' or opt[0] == '--port':
					self.port= int(opt[1])
				if opt[0] == '-e' or opt[0] == '--episodes':
					self.maxEpisodes= int(opt[1])
				if opt[0] == '-m' or opt[0] == '--steps':
					self.maxSteps= int(opt[1])
				if opt[0] == '-r' or opt[0] == '--random':
					self.randomTrackSpeed = True
				if opt[0] == '-x' or opt[0] == '--training':
					self.training = True
				if opt[0] == '-v' or opt[0] == '--version':
					print('%s %s' % (sys.argv[0], version))
					sys.exit(0)
		except ValueError as why:
			print('Bad parameter \'%s\' for option %s: %s\n%s' % (
									   opt[1], opt[0], why, usage))
			sys.exit(-1)
		if len(args) > 0:
			print('Superflous input? %s\n%s' % (', '.join(args), usage))
			sys.exit(-1)

	def get_servers_input(self):
		'''Server's input is stored in a ServerState object'''
		if not self.so: return
		sockdata= str()

		while True:
			try:
				# Receive server data
				sockdata,addr= self.so.recvfrom(data_size)
				sockdata = sockdata.decode('utf-8')
			except socket.error as emsg:
				print('.', end=' ')
				#print "Waiting for data on %d.............." % self.port
			if '***identified***' in sockdata:
				print("Client connected on %d.............." % self.port)
				continue
			elif '***shutdown***' in sockdata:
				print((("Server has stopped the race on %d. "+
						"You were in %d place.") %
						(self.port,self.S.d['racePos'])))
				self.shutdown()
				return
			elif '***restart***' in sockdata:
				# What do I do here?
				print("Server has restarted the race on %d." % self.port)
				# I haven't actually caught the server doing this.
				self.shutdown()
				return
			elif not sockdata: # Empty?
				continue       # Try again.
			else:
				self.S.parse_server_str(sockdata)
				if self.debug:
					sys.stderr.write("\x1b[2J\x1b[H") # Clear for steady output.
					print(self.S)
				break # Can now return from this function.

	def respond_to_server(self):
		if not self.so: return
		try:
			message = repr(self.R)
			self.so.sendto(message.encode(), (self.host, self.port))
		except socket.error as emsg:
			print("Error sending to server: %s Message %s" % (emsg[1],str(emsg[0])))
			sys.exit(-1)
		if self.debug: print(self.R.fancyout())
		# Or use this for plain output:
		#if self.debug: print self.R

	def shutdown(self):
		if not self.so: return
		print(("Race terminated. Shutting down %d."
			   % (self.port)))
		self.so.close()
		self.so = None
		#sys.exit() # No need for this really.

class ServerState():
	'''What the server is reporting right now.'''
	def __init__(self):
		self.servstr= str()
		self.d= dict()

	def parse_server_str(self, server_string):
		'''Parse the server string.'''
		self.servstr= server_string.strip()[:-1]
		sslisted= self.servstr.strip().lstrip('(').rstrip(')').split(')(')
		for i in sslisted:
			w= i.split(' ')
			self.d[w[0]]= destringify(w[1:])

	def __repr__(self):
		# Comment the next line for raw output:
		return self.fancyout()
		# -------------------------------------
		out= str()
		for k in sorted(self.d):
			strout= str(self.d[k])
			if type(self.d[k]) is list:
				strlist= [str(i) for i in self.d[k]]
				strout= ', '.join(strlist)
			out+= "%s: %s\n" % (k,strout)
		return out

	def fancyout(self):
		'''Specialty output for useful ServerState monitoring.'''
		out= str()
		sensors= [ # Select the ones you want in the order you want them.
		#'curLapTime',
		#'lastLapTime',
		'stucktimer',
		#'damage',
		#'focus',
		'fuel',
		#'gear',
		'distRaced',
		'distFromStart',
		#'racePos',
		'opponents',
		'wheelSpinVel',
		'z',
		'speedZ',
		'speedY',
		'speedX',
		'targetSpeed',
		'rpm',
		'skid',
		'slip',
		'track',
		'trackPos',
		'angle',
		]

		#for k in sorted(self.d): # Use this to get all sensors.
		for k in sensors:
			if type(self.d.get(k)) is list: # Handle list type data.
				if k == 'track': # Nice display for track sensors.
					strout= str()
				 #  for tsensor in self.d['track']:
				 #      if   tsensor >180: oc= '|'
				 #      elif tsensor > 80: oc= ';'
				 #      elif tsensor > 60: oc= ','
				 #      elif tsensor > 39: oc= '.'
				 #      #elif tsensor > 13: oc= chr(int(tsensor)+65-13)
				 #      elif tsensor > 13: oc= chr(int(tsensor)+97-13)
				 #      elif tsensor >  3: oc= chr(int(tsensor)+48-3)
				 #      else: oc= '_'
				 #      strout+= oc
				 #  strout= ' -> '+strout[:9] +' ' + strout[9] + ' ' + strout[10:]+' <-'
					raw_tsens= ['%.1f'%x for x in self.d['track']]
					strout+= ' '.join(raw_tsens[:9])+'_'+raw_tsens[9]+'_'+' '.join(raw_tsens[10:])
				elif k == 'opponents': # Nice display for opponent sensors.
					strout= str()
					for osensor in self.d['opponents']:
						if   osensor >190: oc= '_'
						elif osensor > 90: oc= '.'
						elif osensor > 39: oc= chr(int(osensor/2)+97-19)
						elif osensor > 13: oc= chr(int(osensor)+65-13)
						elif osensor >  3: oc= chr(int(osensor)+48-3)
						else: oc= '?'
						strout+= oc
					strout= ' -> '+strout[:18] + ' ' + strout[18:]+' <-'
				else:
					strlist= [str(i) for i in self.d[k]]
					strout= ', '.join(strlist)
			else: # Not a list type of value.
				if k == 'gear': # This is redundant now since it's part of RPM.
					gs= '_._._._._._._._._'
					p= int(self.d['gear']) * 2 + 2  # Position
					l= '%d'%self.d['gear'] # Label
					if l=='-1': l= 'R'
					if l=='0':  l= 'N'
					strout= gs[:p]+ '(%s)'%l + gs[p+3:]
				elif k == 'damage':
					strout= '%6.0f %s' % (self.d[k], bargraph(self.d[k],0,10000,50,'~'))
				elif k == 'fuel':
					strout= '%6.0f %s' % (self.d[k], bargraph(self.d[k],0,100,50,'f'))
				elif k == 'speedX':
					cx= 'X'
					if self.d[k]<0: cx= 'R'
					strout= '%6.1f %s' % (self.d[k], bargraph(self.d[k],-30,300,50,cx))
				elif k == 'speedY': # This gets reversed for display to make sense.
					strout= '%6.1f %s' % (self.d[k], bargraph(self.d[k]*-1,-25,25,50,'Y'))
				elif k == 'speedZ':
					strout= '%6.1f %s' % (self.d[k], bargraph(self.d[k],-13,13,50,'Z'))
				elif k == 'z':
					strout= '%6.3f %s' % (self.d[k], bargraph(self.d[k],.3,.5,50,'z'))
				elif k == 'trackPos': # This gets reversed for display to make sense.
					cx='<'
					if self.d[k]<0: cx= '>'
					strout= '%6.3f %s' % (self.d[k], bargraph(self.d[k]*-1,-1,1,50,cx))
				elif k == 'stucktimer':
					if self.d[k]:
						strout= '%3d %s' % (self.d[k], bargraph(self.d[k],0,300,50,"'"))
					else: strout= 'Not stuck!'
				elif k == 'rpm':
					g= self.d['gear']
					if g < 0:
						g= 'R'
					else:
						g= '%1d'% g
					strout= bargraph(self.d[k],0,10000,50,g)
				elif k == 'angle':
					asyms= [
						  "  !  ", ".|'  ", "./'  ", "_.-  ", ".--  ", "..-  ",
						  "---  ", ".__  ", "-._  ", "'-.  ", "'\.  ", "'|.  ",
						  "  |  ", "  .|'", "  ./'", "  .-'", "  _.-", "  __.",
						  "  ---", "  --.", "  -._", "  -..", "  '\.", "  '|."  ]
					rad= self.d[k]
					deg= int(rad*180/PI)
					symno= int(.5+ (rad+PI) / (PI/12) )
					symno= symno % (len(asyms)-1)
					strout= '%5.2f %3d (%s)' % (rad,deg,asyms[symno])
				elif k == 'skid': # A sensible interpretation of wheel spin.
					frontwheelradpersec= self.d['wheelSpinVel'][0]
					skid= 0
					if frontwheelradpersec:
						skid= .5555555555*self.d['speedX']/frontwheelradpersec - .66124
					strout= bargraph(skid,-.05,.4,50,'*')
				elif k == 'slip': # A sensible interpretation of wheel spin.
					frontwheelradpersec= self.d['wheelSpinVel'][0]
					slip= 0
					if frontwheelradpersec:
						slip= ((self.d['wheelSpinVel'][2]+self.d['wheelSpinVel'][3]) -
							  (self.d['wheelSpinVel'][0]+self.d['wheelSpinVel'][1]))
					strout= bargraph(slip,-5,150,50,'@')
				else:
					strout= str(self.d[k])
			out+= "%s: %s\n" % (k,strout)
		return out

class DriverAction():
	'''What the driver is intending to do (i.e. send to the server).
	Composes something like this for the server:
	(accel 1)(brake 0)(gear 1)(steer 0)(clutch 0)(focus 0)(meta 0) or
	(accel 1)(brake 0)(gear 1)(steer 0)(clutch 0)(focus -90 -45 0 45 90)(meta 0)'''
	def __init__(self):
	   self.actionstr= str()
	   # "d" is for data dictionary.
	   self.d= { 'accel':0.2,
				   'brake':0,
				  'clutch':0,
					'gear':1,
				   'steer':0,
				   'focus':[-90,-45,0,45,90],
					'meta':0
					}

	def clip_to_limits(self):
		"""There pretty much is never a reason to send the server
		something like (steer 9483.323). This comes up all the time
		and it's probably just more sensible to always clip it than to
		worry about when to. The "clip" command is still a snakeoil
		utility function, but it should be used only for non standard
		things or non obvious limits (limit the steering to the left,
		for example). For normal limits, simply don't worry about it."""
		self.d['steer']= clip(self.d['steer'], -1, 1)
		self.d['brake']= clip(self.d['brake'], 0, 1)
		self.d['accel']= clip(self.d['accel'], 0, 1)
		self.d['clutch']= clip(self.d['clutch'], 0, 1)
		if self.d['gear'] not in [-1, 0, 1, 2, 3, 4, 5, 6]:
			self.d['gear']= 0
		if self.d['meta'] not in [0,1]:
			self.d['meta']= 0
		if type(self.d['focus']) is not list or min(self.d['focus'])<-180 or max(self.d['focus'])>180:
			self.d['focus']= 0

	def __repr__(self):
		self.clip_to_limits()
		out= str()
		for k in self.d:
			out+= '('+k+' '
			v= self.d[k]
			if not type(v) is list:
				out+= '%.3f' % v
			else:
				out+= ' '.join([str(x) for x in v])
			out+= ')'
		return out
		return out+'\n'

	def fancyout(self):
		'''Specialty output for useful monitoring of bot's effectors.'''
		out= str()
		od= self.d.copy()
		od.pop('gear','') # Not interesting.
		od.pop('meta','') # Not interesting.
		od.pop('focus','') # Not interesting. Yet.
		for k in sorted(od):
			if k == 'clutch' or k == 'brake' or k == 'accel':
				strout=''
				strout= '%6.3f %s' % (od[k], bargraph(od[k],0,1,50,k[0].upper()))
			elif k == 'steer': # Reverse the graph to make sense.
				strout= '%6.3f %s' % (od[k], bargraph(od[k]*-1,-1,1,50,'S'))
			else:
				strout= str(od[k])
			out+= "%s: %s\n" % (k,strout)
		return out

# == Misc Utility Functions
def destringify(s):
	'''makes a string into a value or a list of strings into a list of
	values (if possible)'''
	if not s: return s
	if type(s) is str:
		try:
			return float(s)
		except ValueError:
			print("Could not find a value in %s" % s)
			return s
	elif type(s) is list:
		if len(s) < 2:
			return destringify(s[0])
		else:
			return [destringify(i) for i in s]


def make_observaton(raw_obs, maxspeed):

	names = ['focus',
			 'speedX', 'speedY', 'speedZ',
			 'opponents',
			 'rpm',
			 'track',
			 'wheelSpinVel',
			 'img', 'trackPos']
	Observation = col.namedtuple('Observaion', names)


	# Get RGB from observation
	image_rgb = obs_vision_to_image_rgb(raw_obs[names[8]])

	return Observation(focus=np.array(raw_obs['focus'], dtype=np.float32)/200.,
					   speedX=np.array(raw_obs['speedX'], dtype=np.float32)/maxspeed,
					   speedY=np.array(raw_obs['speedY'], dtype=np.float32)/maxspeed,
					   speedZ=np.array(raw_obs['speedZ'], dtype=np.float32)/maxspeed,
					   opponents=np.array(raw_obs['opponents'], dtype=np.float32)/200.,
					   rpm=np.array(raw_obs['rpm'], dtype=np.float32),
					   track=np.array(raw_obs['track'], dtype=np.float32)/200.,
					   wheelSpinVel=np.array(raw_obs['wheelSpinVel'], dtype=np.float32),
					   img=image_rgb, trackPos=np.array(raw_obs['trackPos'], dtype=np.float32))


def obs_vision_to_image_rgb(obs_image_vec):
	image_vec = obs_image_vec
	rgb = []
	temp = []
	# convert size 64x64x3 = 12288 to 64x64=4096 2-D list
	# with rgb values grouped together.
	# Format similar to the observation in openai gym
	for i in range(0, 12286, 3):
		temp.append(image_vec[i])
		temp.append(image_vec[i + 1])
		temp.append(image_vec[i + 2])
		rgb.append(temp)
		temp = []
	return np.array(rgb, dtype=np.uint8)


def processImage(vision):
	img = np.ndarray((64, 64, 3))
	for i in range(3):
		img[:, :, i] = 255 - vision[:, i].reshape((64, 64))

	# if next_timestamp is 0 or time.time() > next_timestamp:
	# next_timestamp = time.time() + 10
	#plt.imshow(img, origin='lower')
	#plt.draw()
	#plt.pause(1)
	return img

def drive(c, observate):
	'''This is only an example. It will get around the track but the
	correct thing to do is write your own `drive()` function.'''
	S,R= c.S.d,c.R.d

	observation = make_observaton(S, c.maxSpeed)
	_, _, _, _, _, _, track, _, vision, trackPos = observation
	img = processImage(vision)

	# Steer To Corner
	R['steer'] = S['angle']*10 / PI
	# Steer To Center
	R['steer'] -= S['trackPos']*.10

	# Append observation for later training
	if observate:
		buffer.append((img, R['steer']))

	# do a forward pass to predict value, worry later about fixing this value
	if observate and c.training:
		real_value = R['steer']

		# prepare image
		temp_buff = []
		temp_buff.append(img)
		temp_buff = np.array(temp_buff, dtype='float32')
		if c.network.grayscale:
			temp_buff[0, :, :, 0] = rgb2gray(temp_buff[0])
			temp_buff = reduceDimRGBtoGray(temp_buff)
		temp_buff /= 255.0
		temp_buff -= c.network.meanTrainingInput
		temp_buff = temp_buff.transpose(0, 3, 1, 2)
		img = torch.from_numpy(temp_buff)

		formatted_img = Variable(img.float())
		expectation = c.network.forward(formatted_img).data[0][0]
		R['steer'] = expectation
		#print("Steering, predicated value %f, real value %f" % (R['steer'], real_value))

	# stop the race if lap finished or out of the track
	if track.min() < 0 or S['lastLapTime'] > 0.0:
		return True

	# Throttle Control
	if S['speedX'] < c.maxSpeed - (R['steer']*50):
		R['accel']+= .01
	else:
		R['accel']-= .01

	if S['speedX']< c.maxSpeed / 5.0:
		R['accel'] += .01

	# Traction Control System
	if ((S['wheelSpinVel'][2]+S['wheelSpinVel'][3]) - (S['wheelSpinVel'][0]+S['wheelSpinVel'][1]) > 5):
		R['accel']-= .2

	# Automatic Transmission
	R['gear']=1
	if S['speedX']>50:
		R['gear']=2
	if S['speedX']>80:
		R['gear']=3
	if S['speedX']>110:
		R['gear']=4
	if S['speedX']>140:
		R['gear']=5
	if S['speedX']>170:
		R['gear']=6

	return False

def save_state(filename):
	return
	print("Saving race data %s ..." % (filename))
	with open(filename, 'ab') as file:
		plk.dump(buffer, file)

def syncronizeWithServer(filename):
	return
	sync_instance = Synchronization()

	# send new data
	while True:
		if not sync_instance.fileExistPhysical(filename + ".txt"):
			break

		if sync_instance.upload(filename + ".txt", from_file_path=filename + ".txt", delete_after_upload=True):
			break
		else:
			time.sleep(5)

	# receive new model
	while True:
		if not sync_instance.fileExistBucket(filename + 'model'):
			continue

		if sync_instance.download(filename + 'model', filename + 'model'):
			break
		else:
			print("There is no new network available")
			time.sleep(5)

buffer = []

# ================ MAIN ================
if __name__ == "__main__":

	ignore_steps = 12  # camera is rotating at the beginning
	boolean = [True, False]
	tracks = [1]
	speeds = [30]
	randomTrackSpeed = False
	isTraining = True

	torcs_instance = TorcsInstance()
	sync_instance = Synchronization()

	stop_training_list = []  # tracks that have achieve best results, so we test then manually later

	while True:
		for track in tracks:
			for speed in speeds:
				for wi in boolean:
					for sa in boolean:
						for au in boolean:
							for gs in boolean:
								for wd in [0, 0.2]:
									weight_decay = wd
									batchsize = 128
									numepochs = 100
									learningrate = 2e-4
									weightinit = wi  # false has proved to be the best
									sizeaverage = sa  # false has proved to be the best
									grayscale = gs
									augmentation = au
									preprocess = True
									filename = "/home/drl_rcc_torcs/models/tr%d/#track=%d#speed=%d#wd=%f#bs=%d#ne=%e#wi=%s#sa=%s#au=%s#gs=%s" % \
														   (track, track, speed, weight_decay, batchsize, numepochs,
															weightinit, sizeaverage, augmentation, grayscale)
									if filename in stop_training_list:
										print("best performance already achieve, skipping")
										continue
									modelfile = filename + ".model"
									datafile = filename + ".txt"

									# if sync_instance.fileExistBucket(filename + ".txt") and not sync_instance.fileExistBucket(filename + ".model"):
									# 	# we have uploaded data, then we are waiting for the model
									# 	print ("we have uploaded data, then we are waiting for the model")
									# 	continue
									#
									# if sync_instance.fileExistBucket(modelfile):
									# 	# we have new model, download
									# 	print("downloading new model")
									# 	sync_instance.download(modelfile, modelfile)
									#
									# if sync_instance.fileExistPhysical(filename + ".txt"):
									# 	print("removing old file")
									# 	sync_instance.removeFilePhysical(filename + ".txt")
									#
									# if not os.path.isfile(modelfile):
									# 	# we do not have a model for this setting
									# 	print("model %s does not exist" % modelfile)
									# 	continue

									try:
										C = Client(p=3101, maxspeed=speed, track=track, model="/home/drl_rcc_torcs/models/tr1/#track=1#speed=30#wd=0.000000#bs=128#ne=1.000000e+02#wi=True#sa=True#au=True#gs=True.model")
										isTraining = C.training
										randomTrackSpeed = C.randomTrackSpeed
										buffer = []
										start = time.time()
										steps = 0
										while True:
											C.get_servers_input()
											endrace = drive(C, (steps > ignore_steps))
											C.respond_to_server()
											steps += 1
											if steps == 5000:
												print("Flushing buffer, data count %d" % (len(buffer)))
												save_state(datafile)
												buffer = []
											if endrace:
												end = time.time()
												print("Runned race in %fs, steps %d, data count %d" % (end - start, steps, len(buffer)))
												save_state(datafile)
												buffer = []
												torcs_instance.close()
												if steps > 1400:
													stop_training_list.append(filename)
												if C.training:
													with open("training_log.txt", 'a') as file:
														text = "\nfile = %s, steps = %d " % (modelfile, steps)
														file.write(text)
													sync_instance.upload(filename + '.txt', filename + '.txt')
												break
										C.shutdown()
										torcs_instance.sleep()
									except KeyboardInterrupt:
										pass
			if randomTrackSpeed:
				torcs_instance.changeTrack()
		if not isTraining:
			break

# steering distribution. going much straight?
