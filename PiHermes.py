import time
import os
import sys
import asyncio
import signal
import RPi.GPIO as GPIO


from time import sleep
from telethon import TelegramClient, events, sync
from telethon.tl.types import InputMessagesFilterVoice
from subprocess import Popen
from enum import Enum

"""
Create objects
"""
# https://my.telegram.org/
telegram_api = {
    'api_id': 0,
    'api_hash': 'Enter Api Hash Here'
    }

telegram_sender = {
    'session': 'session',
    'phone': '+555',
    'auth_code': None
    }

telegram_receivers = {
    'Receiver1': {
        'telegram_id': '@id1',
        'gpio_btn': 17,
        'gpio_led': 27
        },
    'Receiver2': {
        'telegram_id': '@id2',
        'gpio_btn': 5,
        'gpio_led': 6
        }
    }


class State(Enum):
    IDLE = 0
    RECORDING = 1
    PLAYING = 2

telegram_states = {crec: dict(msg_waiting=False,
                              play=False,
                              recorded=False)
                   for crec in telegram_receivers}

hermes_props_list = [('telegram_connected', False),
                     ('recording_pid', None),
                     ('playing_pid', None),
                     ('telegram_client', None),
                     ('state', State.IDLE),
                     ('telegram_states', telegram_states)]

hermes_state = type('HermesState', (), {key: value 
                    for (key, value) in hermes_props_list})


def init_gpio(hermes_state):
    """
    initialisation of GPIO leds and switches
    """
    print('Setting up pins ...')
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)

    for ctr in telegram_receivers:
        GPIO.setup(telegram_receivers[ctr]['gpio_led'], GPIO.OUT, initial=GPIO.LOW)

        GPIO.setup(telegram_receivers[ctr]['gpio_btn'], GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.add_event_detect(telegram_receivers[ctr]['gpio_btn'],
                              GPIO.BOTH,
                              callback=lambda channel: gpio_event_handler(channel, hermes_state),
                              bouncetime=100)


def start_recording(ctr, hermes_state):
    if hermes_state.state != State.IDLE: return

    hermes_state.state = State.RECORDING
    GPIO.output(telegram_receivers[ctr]['gpio_led'], GPIO.HIGH)
    print('Starting recording for {:s}'.format(ctr))
    
    if hermes_state.recording_pid is not None:
        hermes_state.recording_pid.kill()
        sleep(1)
        
    hermes_state.recording_pid = Popen(
        ['/usr/bin/arecord',
         '--rate=44000','/home/pi/rec_{:s}.wav'.format(ctr)])
    
    print('Recording started ...')


def stop_recording(ctr, hermes_state):
    print('Entering stop')
    print(hermes_state.recording_pid)
    print(hermes_state.state)
    if hermes_state.recording_pid is None or \
       hermes_state.state != State.RECORDING: return

    hermes_state.recording_pid.kill()

    GPIO.output(telegram_receivers[ctr]['gpio_led'], GPIO.LOW)
    os.system('/usr/bin/sox /home/pi/rec_{:s}.wav /home/pi/rec_{:s}.ogg'.format(ctr, ctr))
    #os.rename('/home/pi/rec_{:s}.ogg'.format(ctr), '/home/pi/rec_{:s}.oga'.format(ctr))

    hermes_state.state = State.IDLE    
    print('Recording stopped')


def channel_to_user(channel):
    for cs in telegram_receivers:
        if telegram_receivers[cs]['gpio_btn'] == channel:
            return cs

    if ctr is None: return
    

def gpio_event_handler(channel, hermes_state):
    ctr = channel_to_user(channel)
    print('Callback Channel {:d}'.format(channel))
    if GPIO.input(channel) > 0:
        print('Button for receiver {:s} pressed'.format(ctr))
        if hermes_state.state == State.IDLE and \
           hermes_state.telegram_states[ctr]['msg_waiting'] is False:

            start_recording(ctr, hermes_state)
        elif hermes_state.state == State.IDLE and \
             hermes_state.telegram_states[ctr]['msg_waiting'] is True:
            
            hermes_state.telegram_states[ctr]['msg_waiting'] = False
            hermes_state.telegram_states[ctr]['play'] = True
    else:
        if hermes_state.state == State.RECORDING:

            stop_recording(ctr, hermes_state)
            hermes_state.telegram_states[ctr]['recorded'] = True
        elif hermes_state.telegram_states[ctr]['msg_waiting'] is True:
            pass


async def init_telegram(hermes_state):
    if hermes_state.telegram_connected: return
    
    print('Telegram client connecting ...')
    hermes_state.telegram_client = TelegramClient(telegram_sender['session']                                                  ,
                                                  telegram_api['api_id'],
                                                  telegram_api['api_hash'])
    await hermes_state.telegram_client.connect()
    asyncio.sleep(2)
    hermes_state.telegram_connected = await hermes_state.telegram_client.is_user_authorized()

    if hermes_state.telegram_connected:
        print('Connected!')
    else:
        print('Authorization needed')
        await hermes_state.telegram_client.send_code_request(telegram_sender['phone'])
        me = await hermes_state.telegram_client.sign_in(telegram_sender['phone'], input('Enter code: '))
        print(me)
        hermes_state.telegram_connected = await hermes_state.telegram_client.is_user_authorized()
        if hermes_state.telegram_connected:
            print('Connected!')
        else:
            print('Authorization failed!')       

    asyncio.sleep(2)
    @hermes_state.telegram_client.on(events.NewMessage)
    async def receive_telegram(event):
        print('Event!')
        fromName = '@' + event.sender.username
        if not event.media.document.mime_type == 'audio/ogg': return
        print('Received message from {:s}'.format(fromName))

        # look for user
        for ctr in telegram_receivers:
            if fromName == telegram_receivers[ctr]['telegram_id']:
                # indicate message by LED
                print('Received message from {:s}'.format(ctr))
     
                hermes_state.telegram_states[ctr]['msg_waiting'] = True
                GPIO.output(telegram_receivers[ctr]['gpio_led'], GPIO.HIGH)
                ad = await hermes_state.telegram_client.download_media(event.media)
                os.rename(ad, '/home/pi/received_from_' + ctr + '.ogg')
                await asyncio.sleep(0.5)
                break


async def send_telegram(hermes_state, ctr):
    while True:
        await asyncio.sleep(2)
        if hermes_state.telegram_states[ctr]['recorded'] is True:
            hermes_state.telegram_states[ctr]['recorded'] = False
            print('Sending telegram to {:s}'.format(ctr))

            await hermes_state.telegram_client.send_file(telegram_receivers[ctr]['telegram_id'],
                                   '/home/pi/rec_{:s}.ogg'.format(ctr),
                                   voice_note=True)
            print('Telegram sent')


async def play_msg(hermes_state, ctr):
    while True:
        await asyncio.sleep(0.2)
        if hermes_state.telegram_states[ctr]['play'] is True:
            hermes_state.telegram_states[ctr]['play'] = False

            hermes_state.state = State.PLAYING
            print('Playing message from {:s}'.format(ctr))
            hermes_state.playing_pid = Popen(
                ['/usr/bin/cvlc',
                 '--play-and-exit','/home/pi/received_from_{:s}.ogg'.format(ctr)])

            hermes_state.playing_pid.wait()
            hermes_state.state = State.IDLE
            GPIO.output(telegram_receivers[ctr]['gpio_led'], GPIO.LOW)

        
"""
Main sequence
"""

if __name__ == '__main__':
    try:
        init_gpio(hermes_state)

        # run the event loop
        loop = asyncio.get_event_loop()
        loop.create_task(init_telegram(hermes_state))
        for ctr in telegram_receivers:
            loop.create_task(send_telegram(hermes_state, ctr))
            loop.create_task(play_msg(hermes_state, ctr))
        loop.run_forever()
        loop.close()
    except :
        print("Error:", sys.exc_info()[0])

    # cleanup
    GPIO.cleanup()


