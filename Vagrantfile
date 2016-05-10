# -*- mode: ruby -*-
# vi: set ft=ruby :

Vagrant.configure(2) do |config|
  config.vm.box = "ubuntu/trusty64"

  # Compiling pandas requires 1Gb of memory
  config.vm.provider "virtualbox" do |v|
    v.memory = 1024
  end

  # Forward ipython notebook's port to the host
  config.vm.network "forwarded_port", guest: 8888, host: 8888

  config.vm.provision "shell", inline: <<-SHELL
    sudo apt-get update
    sudo apt-get install -y autoconf automake build-essential git libfreetype6-dev libpng12-dev libtool nmap pkg-config python-all-dev python-matplotlib python-nose python-numpy python-pip python-zmq sshpass trace-cmd
    sudo pip install ipython[notebook] pandas
    sudo apt-get remove -y w3m

    ln -s /vagrant /home/vagrant/lisa
    chown vagrant.vagrant /home/vagrant/lisa
    echo unset LANG LC_CTYPE >> /home/vagrant/.bashrc
    echo cd /home/vagrant/lisa >> /home/vagrant/.bashrc
    echo source init_env >> /home/vagrant/.bashrc
  SHELL
end
